"""
Creneaux horaires de diffusion des pubs (Reglages -> Pubs planifiees).

A chaque creneau actif correspond une heure et une liste de pubs qui
passeront, l'une apres l'autre, a la fin du titre en cours ce jour-la
(voir rotation._maybe_push_due_pub_slots). Remplace l'ancien reglage
"une pub toutes les M minutes".
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

import database
from auth import login_required

bp = Blueprint("pub_slots", __name__)


def _parse_track_ids():
    ids = []
    for raw in request.form.getlist("track_ids"):
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    return ids


@bp.route("/reglages/creneaux/ajouter", methods=["POST"])
@login_required
def ajouter():
    time_str = request.form.get("time", "").strip()
    if not time_str:
        flash("L'heure du creneau est obligatoire.", "error")
        return redirect(url_for("settings.reglages"))

    database.add_pub_slot(time_str, _parse_track_ids())
    flash("Creneau ajoute.", "success")
    return redirect(url_for("settings.reglages"))


@bp.route("/reglages/creneaux/<int:slot_id>/modifier", methods=["GET", "POST"])
@login_required
def modifier(slot_id):
    slot = database.get_pub_slot(slot_id)
    if not slot:
        flash("Creneau introuvable.", "error")
        return redirect(url_for("settings.reglages"))

    if request.method == "POST":
        time_str = request.form.get("time", "").strip()
        if not time_str:
            flash("L'heure du creneau est obligatoire.", "error")
            return redirect(url_for("pub_slots.modifier", slot_id=slot_id))
        database.update_pub_slot(slot_id, time_str, _parse_track_ids())
        flash("Creneau modifie.", "success")
        return redirect(url_for("settings.reglages"))

    all_pubs = database.list_tracks("pub", active_only=True)
    assigned_ids = set(database.get_pub_slot_track_ids(slot_id))
    return render_template(
        "pub_slot_edit.html", slot=slot, all_pubs=all_pubs, assigned_ids=assigned_ids
    )


@bp.route("/reglages/creneaux/<int:slot_id>/supprimer", methods=["POST"])
@login_required
def supprimer(slot_id):
    database.delete_pub_slot(slot_id)
    flash("Creneau supprime.", "success")
    return redirect(url_for("settings.reglages"))


@bp.route("/reglages/creneaux/<int:slot_id>/activer", methods=["POST"])
@login_required
def activer(slot_id):
    slot = database.get_pub_slot(slot_id)
    if slot:
        database.set_pub_slot_active(slot_id, not slot["active"])
    return redirect(url_for("settings.reglages"))
