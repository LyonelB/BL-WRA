"""
Playlists thematiques (Noel, ete, braderie...) : periodes programmees a
l'avance pendant lesquelles seules les musiques assignees a la playlist
active sont diffusees (remplacement complet de la bibliotheque habituelle),
en plus de la contrainte anti-repetition deja en place (voir Bibliotheque ->
Rotation). Lu en continu par radio.liq (playlists.json, comme
musique_rotation.json) : un changement ici prend effet au plus tard a la
prochaine selection de musique, sans redemarrer Liquidsoap - y compris le
changement de jour qui active/desactive une playlist selon ses dates.

Les dates sont au format jour/mois et se repetent chaque annee (pas besoin
de recreer la playlist "Noel" tous les ans). Si plusieurs playlists actives
se chevauchent a une date donnee, la plus ancienne (id le plus petit, cf.
database.list_playlists) est prioritaire - a eviter en pratique, mais ca ne
bloque rien.
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

import database
import liquidsoap_client
from auth import login_required

bp = Blueprint("playlists", __name__)

_DAYS_IN_MONTH = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def _parse_track_ids():
    ids = []
    for raw in request.form.getlist("track_ids"):
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    return ids


def _parse_bound(prefix):
    """Lit un couple jour/mois du formulaire (ex. 'start_day'/'start_month'),
    renvoie "MM-DD" ou None si invalide (mois hors 1-12, jour hors plage
    pour ce mois - 29 accepte pour fevrier meme les annees non bissextiles,
    pour ne pas avoir a gerer les annees ici : une periode qui inclut le 29
    fevrier est simplement un jour plus large certaines annees)."""
    try:
        day = int(request.form.get(f"{prefix}_day", ""))
        month = int(request.form.get(f"{prefix}_month", ""))
    except ValueError:
        return None
    if month not in _DAYS_IN_MONTH or not (1 <= day <= _DAYS_IN_MONTH[month]):
        return None
    return f"{month:02d}-{day:02d}"


def _sync():
    """Recalcule et reecrit playlists.json depuis la base - a appeler apres
    toute modification affectant la selection cote radio.liq (creation/
    modification/suppression/activation d'une playlist)."""
    liquidsoap_client.write_playlists_file(
        current_app.config["PLAYLISTS_JSON_PATH"],
        database.list_playlists(),
        current_app.config["MUSIQUES_DIR"],
    )


@bp.route("/playlists")
@login_required
def index():
    return render_template(
        "playlists.html",
        playlists=database.list_playlists(),
        all_musiques=database.list_tracks("musique"),
    )


@bp.route("/playlists/ajouter", methods=["POST"])
@login_required
def ajouter():
    name = request.form.get("name", "").strip()
    start_date = _parse_bound("start")
    end_date = _parse_bound("end")
    if not name:
        flash("Le nom de la playlist est obligatoire.", "error")
        return redirect(url_for("playlists.index"))
    if not start_date or not end_date:
        flash("Dates invalides.", "error")
        return redirect(url_for("playlists.index"))

    database.add_playlist(name, start_date, end_date, _parse_track_ids())
    _sync()
    flash("Playlist créée.", "success")
    return redirect(url_for("playlists.index"))


@bp.route("/playlists/<int:playlist_id>/modifier", methods=["GET", "POST"])
@login_required
def modifier(playlist_id):
    playlist = database.get_playlist(playlist_id)
    if not playlist:
        flash("Playlist introuvable.", "error")
        return redirect(url_for("playlists.index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        start_date = _parse_bound("start")
        end_date = _parse_bound("end")
        if not name:
            flash("Le nom de la playlist est obligatoire.", "error")
            return redirect(url_for("playlists.modifier", playlist_id=playlist_id))
        if not start_date or not end_date:
            flash("Dates invalides.", "error")
            return redirect(url_for("playlists.modifier", playlist_id=playlist_id))

        database.update_playlist(playlist_id, name, start_date, end_date, _parse_track_ids())
        _sync()
        flash("Playlist modifiée.", "success")
        return redirect(url_for("playlists.index"))

    all_musiques = database.list_tracks("musique")
    assigned_ids = set(database.get_playlist_track_ids(playlist_id))
    return render_template(
        "playlist_edit.html", playlist=playlist, all_musiques=all_musiques, assigned_ids=assigned_ids
    )


@bp.route("/playlists/<int:playlist_id>/supprimer", methods=["POST"])
@login_required
def supprimer(playlist_id):
    database.delete_playlist(playlist_id)
    _sync()
    flash("Playlist supprimée.", "success")
    return redirect(url_for("playlists.index"))


@bp.route("/playlists/<int:playlist_id>/activer", methods=["POST"])
@login_required
def activer(playlist_id):
    playlist = database.get_playlist(playlist_id)
    if playlist:
        database.set_playlist_active(playlist_id, not playlist["active"])
        _sync()
    return redirect(url_for("playlists.index"))
