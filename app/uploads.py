"""
Gestion des fichiers audio uploades : validation, nom de fichier sur, lecture
des tags (titre/artiste/duree) via mutagen quand c'est possible.
"""

import os
import uuid

from werkzeug.utils import secure_filename

CATEGORY_DIRS = {}  # rempli par app.py a partir de la config (musique/jingle/pub -> dossier)


def allowed_file(filename, allowed_extensions):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def unique_filename(original_name):
    ext = original_name.rsplit(".", 1)[1].lower() if "." in original_name else "mp3"
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


def save_upload(file_storage, category, dest_dir):
    """
    Sauvegarde un fichier uploade dans dest_dir avec un nom unique.
    Renvoie (filename, title, artist, duration).
    """
    os.makedirs(dest_dir, exist_ok=True)
    filename = unique_filename(file_storage.filename)
    dest_path = os.path.join(dest_dir, filename)
    file_storage.save(dest_path)

    title, artist, duration = read_tags(dest_path)
    if not title:
        # a defaut de tag, on part du nom de fichier original comme titre
        title = os.path.splitext(file_storage.filename)[0]

    return filename, title, artist, duration
