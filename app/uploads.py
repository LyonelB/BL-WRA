"""
Gestion des fichiers audio uploades : validation, nom de fichier sur, lecture
des tags (titre/artiste/duree) via mutagen quand c'est possible, conversion
vers un format uniforme via ffmpeg.

Pourquoi convertir : la bibliotheque melangeait des fichiers d'origines
diverses (MP3 44100Hz, MP3 48000Hz, WAV non compresse...), alors que le
pipeline audio de Liquidsoap tourne a frequence d'echantillonnage fixe
(44100Hz, voir radio.liq). A chaque transition vers un fichier d'un format
different, Liquidsoap doit reechantillonner/decoder a la volee, ce qui a ete
identifie comme cause probable des avertissements "Latency is too high"
observes en prod. On normalise donc systematiquement a l'import en MP3
44100Hz/stereo (voir convert_to_mp3 ci-dessous), au bitrate configure dans
Reglages -> Format audio (defaut 192kbps, DEFAULT_BITRATE_KBPS) ; la
bibliotheque deja en ligne peut etre remise a niveau via le bouton
"Relancer la conversion" de cette meme page, ou en ligne de commande avec
convert_library.py (voir library_convert.py pour le moteur partage par les
deux).
"""

import logging
import os
import subprocess
import uuid

from werkzeug.utils import secure_filename

log = logging.getLogger("uploads")

CATEGORY_DIRS = {}  # rempli par app.py a partir de la config (musique/jingle/pub -> dossier)

# Format cible pour toute la bibliotheque (voir docstring du module).
# Frequence/canaux fixes (alignes sur le pipeline Liquidsoap, voir
# radio.liq) : ne pas rendre configurables, c'est justement l'uniformite qui
# evite le reechantillonnage a la volee. Le bitrate, lui, est reglable
# depuis Reglages -> Format audio (voir settings_bp.py) ; DEFAULT_BITRATE_KBPS
# ne sert que de repli si jamais aucun reglage n'est disponible (ex. appel
# direct de convert_to_mp3 hors contexte appli).
TARGET_SAMPLE_RATE = 44100
TARGET_CHANNELS = 2
DEFAULT_BITRATE_KBPS = 192


def allowed_file(filename, allowed_extensions):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def unique_filename(original_name, ext=None):
    ext = ext or (original_name.rsplit(".", 1)[1].lower() if "." in original_name else "mp3")
    safe_base = secure_filename(original_name.rsplit(".", 1)[0]) or "fichier"
    return f"{safe_base}-{uuid.uuid4().hex[:8]}.{ext}"


def read_tags(filepath):
    """Renvoie (title, artist, duration_seconds) au mieux, sans jamais lever."""
    title = artist = None
    duration = None
    try:
        import mutagen

        audio = mutagen.File(filepath, easy=True)
        if audio is not None:
            if audio.tags:
                title = (audio.tags.get("title") or [None])[0]
                artist = (audio.tags.get("artist") or [None])[0]
            if audio.info is not None:
                duration = round(getattr(audio.info, "length", 0) or 0, 1)
    except Exception:
        pass
    return title, artist, duration


def convert_to_mp3(src_path, dest_path, bitrate_kbps=None, timeout=120):
    """
    Convertit src_path en MP3 44100Hz/stereo dans dest_path, au bitrate
    demande (defaut DEFAULT_BITRATE_KBPS), tags ID3 (titre/artiste)
    conserves mais pochette retiree (-vn : elle est stockee comme un flux
    video attache, et alourdit inutilement chaque fichier pour un usage
    radio). Renvoie True/False sans jamais lever : en cas d'echec (ffmpeg
    absent, fichier corrompu...), l'appelant garde le fichier d'origine
    plutot que de faire echouer l'import.
    """
    bitrate_kbps = bitrate_kbps or DEFAULT_BITRATE_KBPS
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-map", "0:a:0", "-vn",
        "-map_metadata", "0",
        "-ar", str(TARGET_SAMPLE_RATE),
        "-ac", str(TARGET_CHANNELS),
        "-b:a", f"{bitrate_kbps}k",
        "-id3v2_version", "3",
        dest_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False
        )
        if result.returncode != 0 or not os.path.exists(dest_path):
            log.warning(
                "Conversion ffmpeg echouee pour %s : %s",
                src_path, result.stderr.decode(errors="replace")[-500:],
            )
            return False
        return True
    except Exception:
        log.exception("Conversion ffmpeg echouee pour %s", src_path)
        return False


def save_upload(file_storage, category, dest_dir, bitrate_kbps=None):
    """
    Sauvegarde un fichier uploade dans dest_dir, converti en MP3 44100Hz/
    stereo au bitrate demande (voir convert_to_mp3 ; bitrate_kbps vient en
    pratique du reglage "audio_convert_bitrate", voir library._upload_view).
    Si la conversion echoue pour une raison quelconque, le fichier d'origine
    est conserve tel quel plutot que de bloquer l'import.
    Renvoie (filename, title, artist, duration).
    """
    os.makedirs(dest_dir, exist_ok=True)

    original_ext = (
        file_storage.filename.rsplit(".", 1)[1].lower()
        if "." in file_storage.filename else "mp3"
    )
    tmp_filename = unique_filename(file_storage.filename, ext=original_ext)
    tmp_path = os.path.join(dest_dir, tmp_filename)
    file_storage.save(tmp_path)

    mp3_filename = tmp_filename if original_ext == "mp3" else unique_filename(
        file_storage.filename, ext="mp3"
    )
    mp3_path = os.path.join(dest_dir, mp3_filename)

    if original_ext == "mp3":
        # On convertit quand meme sur place (frequence/bitrate/pochette
        # potentiellement differents) : fichier temporaire puis remplacement.
        converted_tmp = os.path.join(dest_dir, f".convert-{tmp_filename}")
        ok = convert_to_mp3(tmp_path, converted_tmp, bitrate_kbps=bitrate_kbps)
        if ok:
            os.replace(converted_tmp, tmp_path)
        else:
            if os.path.exists(converted_tmp):
                os.remove(converted_tmp)
        filename = tmp_filename
        final_path = tmp_path
    else:
        ok = convert_to_mp3(tmp_path, mp3_path, bitrate_kbps=bitrate_kbps)
        if ok:
            os.remove(tmp_path)
            filename = mp3_filename
            final_path = mp3_path
        else:
            # Conversion impossible (ex. ffmpeg absent) : on garde le
            # fichier d'origine dans son format natif.
            filename = tmp_filename
            final_path = tmp_path

    title, artist, duration = read_tags(final_path)
    if not title:
        # a defaut de tag, on part du nom de fichier original comme titre
        title = os.path.splitext(file_storage.filename)[0]

    return filename, title, artist, duration
