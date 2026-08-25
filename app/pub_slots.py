"""
Creneaux horaires de diffusion des pubs (page Pubs, section "Pubs
planifiees" - voir library.html pour category == "pub").

A chaque creneau actif correspond une heure, des jours de la semaine, et une
liste de pubs qui passeront, l'une apres l'autre, a la fin du titre en cours
ce jour-la (voir rotation._maybe_push_due_pub_slots). Remplace l'ancien
reglage "une pub toutes les M minutes".
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

import database
from auth import login_required

bp = Blueprint("pub_slots", __name__)

# 1=lundi ... 7=dimanche (datetime.isoweekday()), voir rotation.py
VALID_DAYS = {"1", "2", "3", "4", "5", "6", "7"}


def _parse_track_ids():
    ids = []
    for raw in request.form.getlist("track_ids"):
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    return ids


def _parse_days():
    """Jours coches dans le formulaire (cases 'days'), normalises dans
    l'ordre lundi -> dimanche quel que soit l'ordre de soumission, ex.
    "1,3,5". Chaine vide si aucun jour coche (a rejeter par l'appelant)."""
    days = {d for d in request.form.getlist("days") if d in VALID_DAYS}
    return ",".join(sorted(days, key=int))


@bp.route("/pubs/creneaux/ajouter", methods=["POST"])
@login_required
def ajouter():
    time_str = request.form.get("time", "").strip()
    if not time_str:
        flash("L'heure du créneau est obligatoire.", "error")
        return redirect(url_for("library.pubs"))

    days_str = _parse_days()
    if not days_str:
        flash("Sélectionnez au moins un jour de diffusion.", "error")
        return redirect(url_for("library.pubs"))

    database.add_pub_slot(time_str, days_str, _parse_track_ids())
    flash("Créneau ajouté.", "success")
    return redirect(url_for("library.pubs"))


@bp.route("/pubs/creneaux/<int:slot_id>/modifier", methods=["GET", "POST"])
@login_required
def modifier(slot_id):
    slot = database.get_pub_slot(slot_id)
    if not slot:
        flash("Créneau introuvable.", "error")
        return redirect(url_for("library.pubs"))

    if request.method == "POST":
        time_str = request.form.get("time", "").strip()
        if not time_str:
            flash("L'heure du créneau est obligatoire.", "error")
            return redirect(url_for("pub_slots.modifier", slot_id=slot_id))

        days_str = _parse_days()
        if not days_str:
            flash("Sélectionnez au moins un jour de diffusion.", "error")
            return redirect(url_for("pub_slots.modifier", slot_id=slot_id))

        database.update_pub_slot(slot_id, time_str, days_str, _parse_track_ids())
        flash("Créneau modifié.", "success")
        return redirect(url_for("library.pubs"))

    # Toutes les pubs sont proposees (plus de filtre "active" manuel cote
    # pubs depuis l'introduction du badge "Planifiee", voir library.py).
    all_pubs = database.list_tracks("pub")
    assigned_ids = set(database.get_pub_slot_track_ids(slot_id))
    return render_template(
        "pub_slot_edit.html", slot=slot, all_pubs=all_pubs, assigned_ids=assigned_ids
    )


@bp.route("/pubs/creneaux/<int:slot_id>/supprimer", methods=["POST"])
@login_required
def supprimer(slot_id):
    database.delete_pub_slot(slot_id)
    flash("Créneau supprimé.", "success")
    return redirect(url_for("library.pubs"))


@bp.route("/pubs/creneaux/<int:slot_id>/activer", methods=["POST"])
@login_required
def activer(slot_id):
    slot = database.get_pub_slot(slot_id)
    if slot:
        database.set_pub_slot_active(slot_id, not slot["active"])
    return redirect(url_for("library.pubs"))
