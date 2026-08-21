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
        form_name = request.form.get("form_name", "rotation")

        # Deux <form> distincts sur la page (cartes "Rotation" et "Traitement
        # audio"), chacun marque par un champ cache "form_name" : on ne
        # touche que les reglages du formulaire effectivement soumis, pour
        # ne jamais ecraser l'autre carte avec des valeurs par defaut.
        if form_name == "audio_fx":
            return _save_audio_fx()
        return _save_rotation()

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


def _save_rotation():
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


def _save_audio_fx():
    audio_normalize_enabled = "1" if request.form.get("audio_normalize_enabled") else "0"
    audio_crossfade_enabled = "1" if request.form.get("audio_crossfade_enabled") else "0"
    audio_blank_removal_enabled = "1" if request.form.get("audio_blank_removal_enabled") else "0"

    database.set_setting("audio_normalize_enabled", audio_normalize_enabled)
    database.set_setting("audio_crossfade_enabled", audio_crossfade_enabled)
    database.set_setting("audio_blank_removal_enabled", audio_blank_removal_enabled)

    updated_settings = database.get_all_settings()
    try:
        liquidsoap_client.write_audio_fx_file(
            current_app.config["AUDIO_FX_JSON_PATH"], updated_settings
        )
    except OSError as exc:
        flash(f"Reglages enregistres, mais audio_fx.json n'a pas pu etre ecrit : {exc}", "error")
        return redirect(url_for("settings.reglages"))

    failed = liquidsoap_client.sync_audio_fx(
        current_app.config["LIQUIDSOAP_API_URL"], updated_settings
    )
    if failed:
        flash(
            "Reglages enregistres ; Liquidsoap etait injoignable pour appliquer a chaud : "
            + ", ".join(failed) + " (repris automatiquement a son prochain demarrage).",
            "error",
        )
    else:
        flash("Reglages enregistres.", "success")
    return redirect(url_for("settings.reglages"))
