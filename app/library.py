"""
Gestion de la bibliotheque : musiques, jingles, pubs.

Les 3 sections de l'interface (Bibliotheque / Jingles / Pubs) partagent le
meme code cote serveur, seule la "categorie" change. C'est aussi ce qui fait
que Liquidsoap peut retrouver un fichier : chaque categorie a son propre
dossier (musiques/jingles/pubs) surveille par une playlist Liquidsoap.
"""

import os

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import database
import liquidsoap_client
from auth import login_required
from uploads import allowed_file, save_upload

bp = Blueprint("library", __name__)

CATEGORY_CONFIG = {
    "musique": {
        "dir_key": "MUSIQUES_DIR", "url": "bibliotheque", "label": "Bibliotheque",
        "hint": "Les musiques sont jouees en boucle, dans un ordre aleatoire.",
        "show_jouer": False,
    },
    "jingle": {
        "dir_key": "JINGLES_DIR", "url": "jingles", "label": "Jingles",
        "hint": "Un jingle actif est insere automatiquement toutes les N musiques (regle ci-dessous).",
        "show_jouer": True,
    },
    "pub": {
        "dir_key": "PUBS_DIR", "url": "pubs", "label": "Pubs",
        "hint": "Les pubs actives passent aux creneaux horaires planifies ci-dessous.",
        "show_jouer": True,
    },
}


def _dir_for(category):
    return current_app.config[CATEGORY_CONFIG[category]["dir_key"]]


def _list_view(category):
    search = request.args.get("q", "").strip() or None
    tracks = database.list_tracks(category, search=search)
    counts = database.count_tracks().get(category, {"total": 0, "actifs": 0})
    cfg = CATEGORY_CONFIG[category]

    # Reglages propres a chaque categorie, affiches sous la bibliotheque sur
    # cette meme page (voir library.html) - deplaces depuis Reglages pour
    # regrouper bibliotheque + planification au meme endroit.
    extra = {}
    if category == "jingle":
        extra["jingle_every_n_titles"] = database.get_setting("jingle_every_n_titles", 4)
    elif category == "pub":
        extra["pub_slots"] = database.list_pub_slots()
        extra["active_pubs"] = database.list_tracks("pub", active_only=True)

    return render_template(
        "library.html",
        tracks=tracks,
        counts=counts,
        search=search or "",
        category=category,
        cfg=cfg,
        **extra,
    )


def _upload_view(category):
    cfg = CATEGORY_CONFIG[category]
    files = request.files.getlist("fichiers")
    if not files or files == [None]:
        flash("Aucun fichier selectionne.", "error")
        return redirect(url_for(f"library.{cfg['url']}"))

    allowed = current_app.config["ALLOWED_EXTENSIONS"]
    ok, ko = 0, 0
    for f in files:
        if not f or not f.filename:
            continue
        if not allowed_file(f.filename, allowed):
            ko += 1
            continue
        filename, title, artist, duration = save_upload(f, category, _dir_for(category))
        database.add_track(category, filename, title=title, artist=artist, duration=duration)
        ok += 1

    if ok:
        flash(f"{ok} fichier(s) ajoute(s) a {cfg['label'].lower()}.", "success")
    if ko:
        flash(f"{ko} fichier(s) ignore(s) (format non supporte).", "error")

    return redirect(url_for(f"library.{cfg['url']}"))


def _delete_view(category, track_id):
    track = database.get_track(track_id)
    cfg = CATEGORY_CONFIG[category]
    if track and track["category"] == category:
        path = os.path.join(_dir_for(category), track["filename"])
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            current_app.logger.warning("Suppression fichier impossible (%s): %s", path, exc)
        database.delete_track(track_id)
        flash("Element supprime.", "success")
    return redirect(url_for(f"library.{cfg['url']}"))


def _toggle_view(category, track_id):
    track = database.get_track(track_id)
    cfg = CATEGORY_CONFIG[category]
    if track and track["category"] == category:
        database.set_track_active(track_id, not track["active"])
    return redirect(url_for(f"library.{cfg['url']}"))


def _edit_view(category, track_id):
    track = database.get_track(track_id)
    cfg = CATEGORY_CONFIG[category]
    if not track or track["category"] != category:
        flash("Element introuvable.", "error")
        return redirect(url_for(f"library.{cfg['url']}"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        artist = request.form.get("artist", "").strip()
        database.update_track_metadata(track_id, title, artist)
        flash("Modifications enregistrees.", "success")
        return redirect(url_for(f"library.{cfg['url']}"))

    return render_template("library_edit.html", track=track, cfg=cfg, category=category)


@bp.route("/media/<category>/<path:filename>")
@login_required
def media_file(category, filename):
    """Sert un fichier de la bibliotheque tel quel, pour la preecoute depuis
    la page "Modifier" (voir library_edit.html). Reserve aux comptes
    connectes, comme le reste de l'admin ; send_from_directory se charge de
    refuser toute tentative de sortir du dossier de la categorie."""
    cfg = CATEGORY_CONFIG.get(category)
    if not cfg:
        abort(404)
    return send_from_directory(_dir_for(category), filename)


def _play_now_view(category, track_id):
    """Pour pubs/jingles : injecter immediatement ce fichier dans la file."""
    track = database.get_track(track_id)
    cfg = CATEGORY_CONFIG[category]
    if track and track["category"] == category:
        path = os.path.join(_dir_for(category), track["filename"])
        try:
            liquidsoap_client.push_file(current_app.config["LIQUIDSOAP_API_URL"], path, category=category)
            flash(f"« {track['title'] or track['filename']} » va passer.", "success")
        except liquidsoap_client.LiquidsoapUnavailable:
            flash("Liquidsoap ne repond pas (verifiez que radio.liq tourne).", "error")
    return redirect(url_for(f"library.{cfg['url']}"))


# --- Musiques -----------------------------------------------------------

@bp.route("/bibliotheque")
@login_required
def bibliotheque():
    return _list_view("musique")


@bp.route("/bibliotheque/upload", methods=["POST"])
@login_required
def bibliotheque_upload():
    return _upload_view("musique")


@bp.route("/bibliotheque/<int:track_id>/supprimer", methods=["POST"])
@login_required
def bibliotheque_supprimer(track_id):
    return _delete_view("musique", track_id)


@bp.route("/bibliotheque/<int:track_id>/activer", methods=["POST"])
@login_required
def bibliotheque_activer(track_id):
    return _toggle_view("musique", track_id)


@bp.route("/bibliotheque/<int:track_id>/modifier", methods=["GET", "POST"])
@login_required
def bibliotheque_modifier(track_id):
    return _edit_view("musique", track_id)


# --- Jingles --------------------------------------------------------------

@bp.route("/jingles")
@login_required
def jingles():
    return _list_view("jingle")


@bp.route("/jingles/upload", methods=["POST"])
@login_required
def jingles_upload():
    return _upload_view("jingle")


@bp.route("/jingles/<int:track_id>/supprimer", methods=["POST"])
@login_required
def jingles_supprimer(track_id):
    return _delete_view("jingle", track_id)


@bp.route("/jingles/<int:track_id>/activer", methods=["POST"])
@login_required
def jingles_activer(track_id):
    return _toggle_view("jingle", track_id)


@bp.route("/jingles/<int:track_id>/modifier", methods=["GET", "POST"])
@login_required
def jingles_modifier(track_id):
    return _edit_view("jingle", track_id)


@bp.route("/jingles/<int:track_id>/jouer", methods=["POST"])
@login_required
def jingles_jouer(track_id):
    return _play_now_view("jingle", track_id)


@bp.route("/jingles/reglages", methods=["POST"])
@login_required
def jingles_reglages():
    """Frequence d'insertion automatique des jingles - anciennement dans
    Reglages, deplace ici pour rester avec la bibliotheque de jingles."""
    try:
        jingle_every = max(0, int(request.form.get("jingle_every_n_titles", 4)))
    except ValueError:
        flash("Valeur invalide.", "error")
        return redirect(url_for("library.jingles"))

    database.set_setting("jingle_every_n_titles", jingle_every)
    flash("Reglages enregistres.", "success")
    return redirect(url_for("library.jingles"))


# --- Pubs -------------------------------------------------------------

@bp.route("/pubs")
@login_required
def pubs():
    return _list_view("pub")


@bp.route("/pubs/upload", methods=["POST"])
@login_required
def pubs_upload():
    return _upload_view("pub")


@bp.route("/pubs/<int:track_id>/supprimer", methods=["POST"])
@login_required
def pubs_supprimer(track_id):
    return _delete_view("pub", track_id)


@bp.route("/pubs/<int:track_id>/activer", methods=["POST"])
@login_required
def pubs_activer(track_id):
    return _toggle_view("pub", track_id)


@bp.route("/pubs/<int:track_id>/modifier", methods=["GET", "POST"])
@login_required
def pubs_modifier(track_id):
    return _edit_view("pub", track_id)


@bp.route("/pubs/<int:track_id>/jouer", methods=["POST"])
@login_required
def pubs_jouer(track_id):
    return _play_now_view("pub", track_id)
