"""
Reglages de rotation (frequence des jingles/pubs) et infos station.
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

import database
import liquidsoap_client
from auth import login_required

bp = Blueprint("settings", __name__)


@bp.route("/reglages", methods=["GET", "POST"])
@login_required
def reglages():
    if request.method == "POST":
        try:
            jingle_every = max(0, int(request.form.get("jingle_every_n_titles", 4)))
        except ValueError:
            flash("Valeurs invalides.", "error")
            return redirect(url_for("settings.reglages"))

        station_name = request.form.get("station_name", "").strip() or "Ma Webradio"
        stream_url = request.form.get("stream_url", "").strip()

        database.set_setting("jingle_every_n_titles", jingle_every)
        database.set_setting("station_name", station_name)
        database.set_setting("stream_url", stream_url)
        flash("Reglages enregistres.", "success")
        return redirect(url_for("settings.reglages"))

    settings = database.get_all_settings()
    liquidsoap_status = liquidsoap_client.ping(current_app.config["LIQUIDSOAP_API_URL"])
    relays = database.list_relays()
    pub_slots = database.list_pub_slots()
    active_pubs = database.list_tracks("pub", active_only=True)
    return render_template(
        "reglages.html",
        settings=settings,
        liquidsoap_status=liquidsoap_status,
        relays=relays,
        pub_slots=pub_slots,
        active_pubs=active_pubs,
    )
