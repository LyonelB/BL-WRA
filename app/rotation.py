"""
Logique de rotation : decide quand inserer un jingle ou une pub, a partir
des notifications "nouveau titre" envoyees par Liquidsoap (radio.liq).

Regle v2 :
  - un jingle toutes les N musiques (reglage jingle_every_n_titles, page
    Jingles)
  - les pubs sont programmees a heure fixe ("creneaux", page Pubs -> Pubs
    planifiees) plutot qu'a intervalle : a l'heure du creneau, on pousse
    toutes les pubs cochees pour ce creneau, l'une apres l'autre, a la fin
    du titre en cours (un creneau ne se declenche qu'une fois par jour)
  - a chaque transition, priorite a la pub si un creneau est du, sinon au
    jingle si son compteur est atteint (un seul des deux par transition)

Toute la config est modifiable a chaud depuis l'interface (table settings
et table pub_slots) : pas besoin de redemarrer Liquidsoap ni l'appli.

La categorie (musique/jingle/pub) de ce qui joue est desormais fournie
directement par Liquidsoap dans le payload du webhook (voir radio.liq :
musiques.on_track / prio.on_track), plutot que devinee depuis le chemin du
fichier - plus fiable, notamment a travers crossfade qui ne propage pas
toujours les metadonnees de requete.
"""

import logging
import os
import time
from datetime import datetime

import database
import liquidsoap_client

log = logging.getLogger("rotation")


def handle_track_started(app, metadata):
    """
    Appele depuis la route webhook /api/liquidsoap/on_track a chaque
    nouveau titre diffuse par Liquidsoap.
    """
    category = metadata.get("category") or "inconnu"
    filename = os.path.basename(metadata.get("filename") or "")

    # Liquidsoap ne lit fiablement les tags ID3 que pour certains fichiers
    # (jamais pour le WAV, pas toujours pour un MP3 mal tague) : la
    # bibliotheque (table tracks) a elle un titre garanti (tague a l'upload
    # via mutagen, sinon derive du nom de fichier original - voir
    # uploads.save_upload) et peut avoir ete corrigee a la main via
    # "Modifier". On la prefere donc systematiquement ; le titre/artiste
    # renvoyes par Liquidsoap, puis le nom de fichier, ne servent qu'en
    # dernier recours si la piste n'est plus dans la bibliotheque.
    track = (
        database.get_track_by_filename(category, filename)
        if category in ("musique", "jingle", "pub")
        else None
    )
    title = (track["title"] if track else None) or metadata.get("title") or None
    artist = (track["artist"] if track else None) or metadata.get("artist") or None

    database.log_play(category, filename, title, artist)
    database.set_state("now_playing_category", category)
    database.set_state("now_playing_title", title or filename or "")
    database.set_state("now_playing_artist", artist or "")
    database.set_state("now_playing_since", str(time.time()))

    if category == "musique":
        _maybe_trigger_rotation(app)


def _maybe_trigger_rotation(app):
    jingle_every = int(database.get_setting("jingle_every_n_titles", 4) or 4)
    since_jingle = int(database.get_state("since_last_jingle", 0) or 0) + 1

    base_url = app.config["LIQUIDSOAP_API_URL"]

    # Priorite aux pubs planifiees si un creneau est du. On ne verifie le
    # jingle que si aucune pub n'a ete poussee a cette transition, pour ne
    # pas les faire passer au meme endroit.
    pushed_pub = _maybe_push_due_pub_slots(app, base_url)

    jingle_due = jingle_every > 0 and since_jingle >= jingle_every
    if not pushed_pub and jingle_due:
        track = database.random_active_track("jingle")
        if track and _push(base_url, app.config["JINGLES_DIR"], track, "jingle"):
            since_jingle = 0

    database.set_state("since_last_jingle", since_jingle)


def _maybe_push_due_pub_slots(app, base_url):
    """
    Creneaux horaires (Pubs -> Pubs planifiees) : des que l'heure d'un
    creneau actif est passee et qu'il ne s'est pas encore declenche
    aujourd'hui, on pousse toutes les pubs cochees pour ce creneau (encore
    actives), dans l'ordre. Comme ce webhook n'arrive qu'entre deux
    musiques, l'insertion attend bien la fin du titre en cours.

    Si le service a ete arrete pendant l'heure d'un ou plusieurs creneaux,
    ils se declenchent tous a la suite au prochain titre (pas de rattrapage
    plus complique que ca).
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_hm = now.strftime("%H:%M")
    weekday = str(now.isoweekday())  # 1=lundi ... 7=dimanche

    pushed_any = False
    for slot in database.due_pub_slots(now_hm, today, weekday):
        for track in database.get_pub_slot_tracks(slot["id"]):
            if _push(base_url, app.config["PUBS_DIR"], track, "pub"):
                pushed_any = True
        database.mark_pub_slot_fired(slot["id"], today)

    return pushed_any


def _push(base_url, directory, track_row, category):
    filepath = os.path.join(directory, track_row["filename"])
    try:
        liquidsoap_client.push_file(base_url, filepath, category=category)
        return True
    except liquidsoap_client.LiquidsoapUnavailable:
        log.warning("Impossible de pousser %s : Liquidsoap ne repond pas", filepath)
        return False


def now_playing_snapshot():
    return {
        "category": database.get_state("now_playing_category", "inconnu"),
        "title": database.get_state("now_playing_title", ""),
        "artist": database.get_state("now_playing_artist", ""),
        "since": float(database.get_state("now_playing_since", 0) or 0),
    }
