"""
Moteur partage de conversion de la bibliotheque (voir uploads.py pour le
"pourquoi" : uniformiser musiques/jingles/pubs en MP3 44100Hz/stereo pour
eviter le reechantillonnage a la volee par Liquidsoap).

Utilise par deux appelants :
  - convert_library.py, script CLI a lancer manuellement en SSH ;
  - settings_bp.py, bouton "Relancer la conversion" de Reglages -> Format
    audio (execute en arriere-plan, voir _run_conversion_background).

Les deux partagent exactement la meme logique ; seule la connexion SQLite
differe (sqlite3.connect direct pour le CLI, database.get_db() - qui est
elle aussi une connexion sqlite3 standard, meme schema - pour l'appli web).
C'est pour ca que toutes les fonctions ci-dessous prennent "db" en
parametre plutot que d'importer database.py (qui suppose un contexte Flask
actif via current_app/g, absent quand le script tourne seul).
"""

import json
import os
import subprocess

import config
import uploads

CATEGORY_DIRS = {
    "musique": config.MUSIQUES_DIR,
    "jingle": config.JINGLES_DIR,
    "pub": config.PUBS_DIR,
}

# Tolerance sur le bitrate detecte par ffprobe avant de considerer qu'un
# fichier deja MP3/44100Hz/stereo "colle" au bitrate cible : l'encodage reel
# (VBR, arrondis lame) ne tombe pas toujours pile sur la valeur demandee, on
# ne veut pas reconvertir en boucle un fichier deja essentiellement bon.
BITRATE_TOLERANCE_KBPS = 16


def probe(filepath):
    """Renvoie les caracteristiques du premier flux audio (codec, frequence,
    canaux, presence d'un flux video/pochette, bitrate global, duree), ou
    None si l'analyse echoue (fichier manquant/corrompu)."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_streams", "-show_format", filepath,
            ],
            capture_output=True, timeout=30, check=True,
        )
        data = json.loads(result.stdout)
    except Exception:
        return None

    streams = data.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    has_video = any(s.get("codec_type") == "video" for s in streams)
    if not audio:
        return None

    fmt = data.get("format", {})
    duration = None
    try:
        duration = round(float(fmt.get("duration", 0)), 1)
    except (TypeError, ValueError):
        pass

    bit_rate_kbps = None
    raw_bitrate = fmt.get("bit_rate") or audio.get("bit_rate")
    try:
        if raw_bitrate is not None:
            bit_rate_kbps = round(int(raw_bitrate) / 1000)
    except (TypeError, ValueError):
        pass

    return {
        "codec_name": audio.get("codec_name"),
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": audio.get("channels"),
        "has_video": has_video,
        "duration": duration,
        "bit_rate_kbps": bit_rate_kbps,
    }


def needs_conversion(filename, info, bitrate_kbps):
    if info is None:
        return False  # illisible : on ne touche pas, signale a part
    ext_ok = filename.lower().endswith(".mp3")
    bitrate_ok = (
        info["bit_rate_kbps"] is None
        or abs(info["bit_rate_kbps"] - bitrate_kbps) <= BITRATE_TOLERANCE_KBPS
    )
    return not (
        ext_ok
        and info["codec_name"] == "mp3"
        and info["sample_rate"] == uploads.TARGET_SAMPLE_RATE
        and info["channels"] == uploads.TARGET_CHANNELS
        and not info["has_video"]
        and bitrate_ok
    )


def _format_detail(info):
    detail = f"{info['sample_rate']}Hz/{info['channels']}ch/{info['codec_name']}"
    if info["bit_rate_kbps"]:
        detail += f"/{info['bit_rate_kbps']}kbps"
    if info["has_video"]:
        detail += "+pochette"
    return detail


def convert_one(db, category, track, dry_run, bitrate_kbps, on_line=None):
    """Traite un track (dict-like avec id/filename/duration). Renvoie un
    statut parmi : missing, unreadable, skipped, would_convert, converted,
    failed."""
    directory = CATEGORY_DIRS[category]
    filename = track["filename"]
    src_path = os.path.join(directory, filename)

    def line(msg):
        if on_line:
            on_line(msg)

    if not os.path.exists(src_path):
        line(f"  [{category}] MANQUANT : {filename} (ignore)")
        return "missing"

    info = probe(src_path)
    if info is None:
        line(f"  [{category}] ILLISIBLE : {filename} (ignore, verifier le fichier manuellement)")
        return "unreadable"

    if not needs_conversion(filename, info, bitrate_kbps):
        line(f"  [{category}] deja au format cible : {filename}")
        return "skipped"

    detail = _format_detail(info)
    if dry_run:
        line(f"  [{category}] A CONVERTIR : {filename} ({detail} -> 44100Hz/2ch/mp3/{bitrate_kbps}kbps)")
        return "would_convert"

    new_filename = filename if filename.lower().endswith(".mp3") else (
        os.path.splitext(filename)[0] + ".mp3"
    )
    # Nom temporaire pour ne jamais laisser un fichier a moitie ecrit visible
    # sous le nom final (Liquidsoap scanne le dossier en continu).
    tmp_path = os.path.join(directory, f".convert-{new_filename}")

    ok = uploads.convert_to_mp3(src_path, tmp_path, bitrate_kbps=bitrate_kbps)
    if not ok:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        line(f"  [{category}] ECHEC conversion : {filename} (ignore, verifier manuellement)")
        return "failed"

    new_path = os.path.join(directory, new_filename)
    os.replace(tmp_path, new_path)
    if new_path != src_path:
        os.remove(src_path)

    new_info = probe(new_path)
    new_duration = new_info["duration"] if new_info else track["duration"]

    db.execute(
        "UPDATE tracks SET filename = ?, duration = ? WHERE id = ?",
        (new_filename, new_duration, track["id"]),
    )
    db.commit()

    line(f"  [{category}] converti : {filename} ({detail}) -> {new_filename} (44100Hz/2ch/mp3/{bitrate_kbps}kbps)")
    return "converted"


def run(db, dry_run=False, bitrate_kbps=None, on_line=None, on_progress=None):
    """
    Parcourt toute la bibliotheque (musiques/jingles/pubs) et convertit ce
    qui ne colle pas au format cible. on_line(str) recoit une ligne de log
    par fichier/categorie (pour affichage/print) ; on_progress(i, total) est
    appele apres chaque fichier traite (pour une barre de progression).
    Renvoie (counts, total) ou counts est un dict statut -> nombre.
    """
    bitrate_kbps = bitrate_kbps or uploads.DEFAULT_BITRATE_KBPS

    all_tracks = []
    for category, directory in CATEGORY_DIRS.items():
        if not os.path.isdir(directory):
            continue
        tracks = db.execute(
            "SELECT id, filename, duration FROM tracks WHERE category = ? ORDER BY id",
            (category,),
        ).fetchall()
        all_tracks.extend((category, t) for t in tracks)

    total = len(all_tracks)
    counts = {}
    current_category = None
    for i, (category, track) in enumerate(all_tracks, start=1):
        if on_line and category != current_category:
            current_category = category
            on_line(f"-- {category} --")
        status = convert_one(db, category, track, dry_run, bitrate_kbps, on_line=on_line)
        counts[status] = counts.get(status, 0) + 1
        if on_progress:
            on_progress(i, total)

    if not dry_run and total:
        # Memorise le bitrate reellement applique a toute la bibliotheque :
        # permet a Reglages de savoir si un ecart existe avec le bitrate
        # cible configure (audio_convert_bitrate) et donc si le bouton
        # "Relancer la conversion" doit s'afficher.
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('audio_library_applied_bitrate', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(bitrate_kbps),),
        )
        db.commit()

    return counts, total
