"""
Planification des pubs (page Pubs -> Planification, 29/08).

Troisieme brique du systeme de pubs, separee de :
- la Bibliotheque (page Pubs) : ou l'on stocke les fichiers pubs ;
- les Creneaux (section "Creneaux" de la page Pubs) : les heures possibles
  de diffusion (independantes de toute pub particuliere).

Une planification associe UNE pub a une periode de validite precise
(start_date/end_date, "AAAA-MM-JJ", meme convention que les Playlists
musique) et a l'ensemble des creneaux ou elle doit passer PENDANT cette
periode. Plusieurs planifications peuvent partager le meme creneau
(plusieurs pubs passent alors les unes a la suite des autres a ce creneau)
et/ou se chevaucher dans le temps - voir rotation._maybe_push_due_pub_slots
pour le declenchement reel, base sur database.get_due_slot_tracks.

Contrairement aux Playlists, rien a re-ecrire cote Liquidsoap : le
declenchement des pubs se fait entierement cote Python (webhook
on_track -> rotation.py), pas de fichier JSON a resynchroniser ici.
"""

from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

import database
from auth import login_required

bp = Blueprint("pub_campaigns", __name__)


def _parse_slot_ids():
    ids = []
    for raw in request.form.getlist("slot_ids"):
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    return ids


def _parse_bound(prefix):
    """Lit un triplet jour/mois/annee du formulaire (ex. 'start_day'/
    'start_month'/'start_year'), renvoie "AAAA-MM-JJ" ou None si invalide
    (mois hors 1-12, jour inexistant pour ce mois cette annee-la, ou annee
    hors d'une plage raisonnable) - meme logique que playlists.py."""
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


def _parse_track_id():
    raw = request.form.get("track_id", "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


@bp.route("/pubs/planification")
@login_required
def index():
    return render_template(
        "pub_campaigns.html",
        campaigns=database.list_pub_campaigns(),
        all_pubs=database.list_tracks("pub"),
        all_slots=database.list_pub_slots(),
    )


@bp.route("/pubs/planification/ajouter", methods=["POST"])
@login_required
def ajouter():
    track_id = _parse_track_id()
    start_date = _parse_bound("start")
    end_date = _parse_bound("end")
    slot_ids = _parse_slot_ids()

    if not track_id:
        flash("Choisissez une pub.", "error")
        return redirect(url_for("pub_campaigns.index"))
    if not start_date or not end_date:
        flash("Dates invalides.", "error")
        return redirect(url_for("pub_campaigns.index"))
    if end_date < start_date:
        flash("La date de fin est avant la date de début.", "error")
        return redirect(url_for("pub_campaigns.index"))
    if not slot_ids:
        flash("Sélectionnez au moins un créneau.", "error")
        return redirect(url_for("pub_campaigns.index"))

    database.add_pub_campaign(track_id, start_date, end_date, slot_ids)
    flash("Planification créée.", "success")
    return redirect(url_for("pub_campaigns.index"))


@bp.route("/pubs/planification/<int:campaign_id>/modifier", methods=["GET", "POST"])
@login_required
def modifier(campaign_id):
    campaign = database.get_pub_campaign(campaign_id)
    if not campaign:
        flash("Planification introuvable.", "error")
        return redirect(url_for("pub_campaigns.index"))

    if request.method == "POST":
        track_id = _parse_track_id()
        start_date = _parse_bound("start")
        end_date = _parse_bound("end")
        slot_ids = _parse_slot_ids()

        if not track_id:
            flash("Choisissez une pub.", "error")
            return redirect(url_for("pub_campaigns.modifier", campaign_id=campaign_id))
        if not start_date or not end_date:
            flash("Dates invalides.", "error")
            return redirect(url_for("pub_campaigns.modifier", campaign_id=campaign_id))
        if end_date < start_date:
            flash("La date de fin est avant la date de début.", "error")
            return redirect(url_for("pub_campaigns.modifier", campaign_id=campaign_id))
        if not slot_ids:
            flash("Sélectionnez au moins un créneau.", "error")
            return redirect(url_for("pub_campaigns.modifier", campaign_id=campaign_id))

        database.update_pub_campaign(campaign_id, track_id, start_date, end_date, slot_ids)
        flash("Planification modifiée.", "success")
        return redirect(url_for("pub_campaigns.index"))

    all_pubs = database.list_tracks("pub")
    all_slots = database.list_pub_slots()
    assigned_slot_ids = set(database.get_pub_campaign_slot_ids(campaign_id))
    return render_template(
        "pub_campaign_edit.html",
        campaign=campaign,
        all_pubs=all_pubs,
        all_slots=all_slots,
        assigned_slot_ids=assigned_slot_ids,
    )


@bp.route("/pubs/planification/<int:campaign_id>/supprimer", methods=["POST"])
@login_required
def supprimer(campaign_id):
    database.delete_pub_campaign(campaign_id)
    flash("Planification supprimée.", "success")
    return redirect(url_for("pub_campaigns.index"))


@bp.route("/pubs/planification/<int:campaign_id>/activer", methods=["POST"])
@login_required
def activer(campaign_id):
    campaign = database.get_pub_campaign(campaign_id)
    if campaign:
        database.set_pub_campaign_active(campaign_id, not campaign["active"])
    return redirect(url_for("pub_campaigns.index"))
