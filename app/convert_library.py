#!/usr/bin/env python3
"""
Script de maintenance ponctuel : convertit en MP3 192kbps/44100Hz/stereo
(sans pochette) tous les fichiers de la bibliotheque (musiques/jingles/pubs)
qui ne sont pas deja dans ce format, et met a jour la base SQLite en
consequence (colonnes filename/duration de la table tracks).

Pourquoi : depuis l'ajout de la conversion automatique a l'import (voir
uploads.save_upload), tout nouvel ajout est deja uniformise. Ce script
traite la bibliotheque deja en ligne avant ce changement (melange de MP3
44100/48000Hz et de WAV non compresse), soupconnee de causer les
avertissements "Latency is too high" de Liquidsoap a chaque transition vers
un fichier d'un format different du pipeline audio (fixe a 44100Hz, voir
radio.liq).

Usage (sur le Raspberry Pi, une fois l'appli deployee) :

    cd /opt/radio/app
    sudo -u radio venv/bin/python3 convert_library.py --dry-run   # apercu
    sudo -u radio venv/bin/python3 convert_library.py             # execution

IMPORTANT : a executer avec l'utilisateur "radio" (sudo -u radio), pas en
root - sinon les fichiers convertis appartiendraient a root et l'appli web
(qui tourne en tant que "radio") ne pourrait plus les supprimer/deplacer
ensuite. Le script est idempotent (relancer ne reconvertit pas ce qui est
deja au bon format) : en cas d'interruption (Ctrl+C, coupure), on peut le
relancer sans risque.

Sans danger pour le flux en cours : chaque fichier est reencode dans un
fichier temporaire puis bascule seulement une fois pret ; si un fichier en
cours de lecture par Liquidsoap est remplace/supprime pendant l'operation,
la lecture en cours n'est pas interrompue (sous Linux, un fichier ouvert
reste lisible par le processus qui l'a ouvert meme apres suppression/
remplacement sur le disque) - la prochaine lecture de ce titre utilisera la
version convertie.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import uploads

CATEGORY_DIRS = {
    "musique": config.MUSIQUES_DIR,
    "jingle": config.JINGLES_DIR,
    "pub": config.PUBS_DIR,
}


def probe(filepath):
    """Renvoie (codec_name, sample_rate, channels, has_video, duration) du
    premier flux audio, ou None si l'analyse echoue (fichier manquant/
    corrompu)."""
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
    duration = None
    try:
        duration = round(float(data.get("format", {}).get("duration", 0)), 1)
    except (TypeError, ValueError):
        pass
    return {
        "codec_name": audio.get("codec_name"),
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": audio.get("channels"),
        "has_video": has_video,
        "duration": duration,
    }


def needs_conversion(filename, info):
    if info is None:
        return False  # illisible : on ne touche pas, signale a part
    ext_ok = filename.lower().endswith(".mp3")
    return not (
        ext_ok
        and info["codec_name"] == "mp3"
        and info["sample_rate"] == uploads.TARGET_SAMPLE_RATE
        and info["channels"] == uploads.TARGET_CHANNELS
        and not info["has_video"]
    )


def convert_one(db, category, track, dry_run):
    directory = CATEGORY_DIRS[category]
    filename = track["filename"]
    src_path = os.path.join(directory, filename)

    if not os.path.exists(src_path):
        print(f"  [{category}] MANQUANT : {filename} (ignore)")
        return "missing"

    info = probe(src_path)
    if info is None:
        print(f"  [{category}] ILLISIBLE : {filename} (ignore, verifier le fichier manuellement)")
        return "unreadable"

    if not needs_conversion(filename, info):
        print(f"  [{category}] deja au format cible : {filename}")
        return "skipped"

    detail = f"{info['sample_rate']}Hz/{info['channels']}ch/{info['codec_name']}" + (
        "+pochette" if info["has_video"] else ""
    )
    if dry_run:
        print(f"  [{category}] A CONVERTIR : {filename} ({detail} -> 44100Hz/2ch/mp3)")
        return "would_convert"

    new_filename = filename if filename.lower().endswith(".mp3") else (
        os.path.splitext(filename)[0] + ".mp3"
    )
    # Nom temporaire pour ne jamais laisser un fichier a moitie ecrit visible
    # sous le nom final (Liquidsoap scanne le dossier en continu).
    tmp_path = os.path.join(directory, f".convert-{new_filename}")

    ok = uploads.convert_to_mp3(src_path, tmp_path)
    if not ok:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(f"  [{category}] ECHEC conversion : {filename} (ignore, verifier manuellement)")
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

    print(f"  [{category}] converti : {filename} ({detail}) -> {new_filename} (44100Hz/2ch/mp3)")
    return "converted"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Analyse seulement, ne convertit/modifie rien.")
    args = parser.parse_args()

    if not os.path.exists(config.DB_PATH):
        print(f"Base introuvable : {config.DB_PATH}", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row

    counts = {}
    for category, directory in CATEGORY_DIRS.items():
        if not os.path.isdir(directory):
            continue
        tracks = db.execute(
            "SELECT id, filename, duration FROM tracks WHERE category = ? ORDER BY id",
            (category,),
        ).fetchall()
        if not tracks:
            continue
        print(f"-- {category} ({len(tracks)} fichier(s)) --")
        for track in tracks:
            status = convert_one(db, category, track, args.dry_run)
            counts[status] = counts.get(status, 0) + 1

    db.close()

    print()
    print("Resume :", ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "aucun fichier")
    if args.dry_run and counts.get("would_convert"):
        print("Aucun fichier modifie (--dry-run). Relancer sans --dry-run pour convertir.")


if __name__ == "__main__":
    main()
