"""
Playlists thematiques (Noel, ete, braderie...) : periodes programmees a
l'avance pendant lesquelles seules les musiques assignees a la playlist
active sont diffusees (remplacement complet de la bibliotheque habituelle),
en plus de la contrainte anti-repetition deja en place (voir Bibliotheque ->
Rotation). Lu en continu par radio.liq (playlists.json, comme
musique_rotation.json) : un changement ici prend effet au plus tard a la
prochaine selection de musique, sans redemarrer Liquidsoap - y compris le
changement de jour qui active/desactive une playlist selon ses dates.

Les dates sont des periodes precises (jour/mois/annee) : une playlist ne se
repete pas automatiquement d'une annee sur l'autre. Pour une periode qui
revient chaque annee (Noel, ete...), il faut mettre a jour les annees
depuis "Modifier", ou creer une nouvelle playlist pour l'annee suivante. Si
plusieurs playlists actives se chevauchent a une date donnee, la plus
ancienne (id le plus petit, cf. database.list_playlists) est prioritaire -
a eviter en pratique, mais ca ne bloque rien.

EXCLUSIVITE (28/08) : une musique assignee a une playlist activee (le
bouton actif/inactif de la playlist, independant de sa fenetre de dates)
est reservee a cette playlist - elle ne tourne PLUS dans la rotation
normale en dehors de sa periode (voir liquidsoap_client.write_playlists_file
et library_files dans radio.liq). Desactiver la playlist (sans la
supprimer) rend immediatement ses musiques a la rotation normale.
"""

from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

import database
import liquidsoap_client
from auth import login_required

bp = Blueprint("playlists", __name__)


def _parse_track_ids():
    ids = []
    for raw in request.form.getlist("track_ids"):
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    return ids


def _parse_bound(prefix):
    """Lit un triplet jour/mois/annee du formulaire (ex. 'start_day'/
    'start_month'/'start_year'), renvoie "AAAA-MM-JJ" ou None si invalide
    (mois hors 1-12, jour inexistant pour ce mois cette annee-la - dont le
    29 fevrier hors annee bissextile -, ou annee hors d'une plage raisonnable).
    """
    try:
        day = int(request.form.get(f"{prefix}_day", ""))
        month = int(request.form.get(f"{prefix}_month", ""))
        year = int(request.form.get(f"{prefix}_year", ""))
    except ValueError:
        return None
    if not (2000 <= year <= 2100):
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


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
    if end_date < start_date:
        flash(
            "La date de fin est avant la date de début - pour une période qui "
            "chevauche le 1er janvier (ex. Noël), mettez l'année suivante sur "
            "la date de fin.",
            "error",
        )
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
        if end_date < start_date:
            flash(
                "La date de fin est avant la date de début - pour une période qui "
                "chevauche le 1er janvier (ex. Noël), mettez l'année suivante sur "
                "la date de fin.",
                "error",
            )
            return redirect(url_for("playlists.modifier", playlist_id=playlist_id))

        database.update_playlist(playlist_id, name, start_date, end_date, _parse_track_ids())
        _sync()
        flash("Playlist modifiée.", "success")
        return redirect(url_for("playlists.index"))

    all_musiques = database.list_tracks("musique")
    assigned_ids = set(database.get_playlist_track_ids(playlist_id))
    # ?modal=1 : voir library.py/_edit_view pour l'explication (pop-up de
    # modification, base.html/openEditModal).
    modal = request.args.get("modal") == "1"
    return render_template(
        "playlist_edit.html",
        playlist=playlist,
        all_musiques=all_musiques,
        assigned_ids=assigned_ids,
        modal=modal,
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
