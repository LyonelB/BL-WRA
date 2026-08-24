"""
Reglages generaux (infos station) et traitement audio. La rotation des
jingles vit desormais sur la page Jingles (voir library.jingles_reglages)
et la planification des pubs sur la page Pubs (voir pub_slots.py).
"""

import subprocess

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

import database
import liquidsoap_client
from auth import login_required

bp = Blueprint("settings", __name__)


def _liquidsoap_service_since():
    """Depuis quand liquidsoap-radio.service tourne (ActiveEnterTimestamp),
    pour affichage seulement (voir /api/liquidsoap/status). Simple lecture
    d'etat systemd, contrairement au redemarrage (redemarrer_liquidsoap
    ci-dessous) ca ne necessite pas le sudo dedie de install/radio-sudoers."""
    try:
        result = subprocess.run(
            ["systemctl", "show", "liquidsoap-radio", "--property=ActiveEnterTimestamp", "--value"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    value = (result.stdout or "").strip()
    return value or None


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
    liquidsoap_since = _liquidsoap_service_since()
    relays = database.list_relays()
    crossfade_needs_restart = settings.get("audio_crossfade_pending_restart") == "1"
    return render_template(
        "reglages.html",
        settings=settings,
        liquidsoap_status=liquidsoap_status,
        liquidsoap_since=liquidsoap_since,
        relays=relays,
        crossfade_needs_restart=crossfade_needs_restart,
    )


@bp.route("/api/liquidsoap/status")
@login_required
def api_liquidsoap_status():
    """Statut Liquidsoap en JSON, interroge en JavaScript depuis la page
    Reglages (voir reglages.html) pour rafraichir la pastille et l'heure de
    dernier demarrage sans recharger la page - utile juste apres un clic sur
    "Redemarrer Liquidsoap maintenant" : le redemarrage prend quelques
    secondes, la pastille etait jusqu'ici figee sur l'etat au chargement."""
    online = liquidsoap_client.ping(current_app.config["LIQUIDSOAP_API_URL"])
    return jsonify({"online": bool(online), "since": _liquidsoap_service_since()})


@bp.route("/reglages/redemarrer-liquidsoap", methods=["POST"])
@login_required
def redemarrer_liquidsoap():
    """Redemarre liquidsoap-radio depuis l'interface (necessaire pour
    appliquer un changement de fondu enchaine, voir audio_fx_initial dans
    radio.liq). Le service radio-web tourne en tant qu'utilisateur "radio",
    qui doit avoir le droit sudo dedie (voir install/radio-sudoers)."""
    try:
        subprocess.run(
            ["sudo", "/usr/bin/systemctl", "restart", "liquidsoap-radio"],
            check=True, capture_output=True, timeout=15, text=True,
        )
        database.set_setting("audio_crossfade_pending_restart", "0")
        flash("Liquidsoap redemarre.", "success")
    except subprocess.CalledProcessError as exc:
        flash(
            "Echec du redemarrage de Liquidsoap : "
            + (exc.stderr.strip() if exc.stderr else str(exc))
            + ". Verifiez que /etc/sudoers.d/radio-wra est bien installe (voir install/radio-sudoers).",
            "error",
        )
    except subprocess.TimeoutExpired:
        flash("Le redemarrage de Liquidsoap prend plus de temps que prevu, verifiez manuellement.", "error")
    except FileNotFoundError:
        flash("Commande 'sudo' introuvable sur ce serveur.", "error")
    return redirect(url_for("settings.reglages"))


def _save_rotation():
    # La rotation des jingles (jingle_every_n_titles) et la planification des
    # pubs (creneaux) ont leur propre formulaire directement sur les pages
    # Jingles/Pubs (voir library.jingles_reglages et pub_slots.py) - il ne
    # reste ici que les infos generales de la station.
    station_name = request.form.get("station_name", "").strip() or "Ma Webradio"
    stream_url = request.form.get("stream_url", "").strip()

    database.set_setting("station_name", station_name)
    database.set_setting("stream_url", stream_url)
    flash("Reglages enregistres.", "success")
    return redirect(url_for("settings.reglages"))


def _save_audio_fx():
    old_crossfade = database.get_setting("audio_crossfade_enabled", "1")

    audio_normalize_enabled = "1" if request.form.get("audio_normalize_enabled") else "0"
    audio_crossfade_enabled = "1" if request.form.get("audio_crossfade_enabled") else "0"
    audio_blank_removal_enabled = "1" if request.form.get("audio_blank_removal_enabled") else "0"

    database.set_setting("audio_normalize_enabled", audio_normalize_enabled)
    database.set_setting("audio_crossfade_enabled", audio_crossfade_enabled)
    database.set_setting("audio_blank_removal_enabled", audio_blank_removal_enabled)

    # Le fondu enchaine n'est relu qu'au demarrage de Liquidsoap (voir
    # radio.liq) : s'il a change, on memorise qu'un redemarrage reste a
    # faire (bouton "Redemarrer Liquidsoap maintenant" ci-dessous), jusqu'a
    # ce qu'il soit effectivement declenche.
    if audio_crossfade_enabled != old_crossfade:
        database.set_setting("audio_crossfade_pending_restart", "1")

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
    elif audio_crossfade_enabled != old_crossfade:
        flash("Reglages enregistres. Le fondu enchaine a change : redemarrez Liquidsoap pour l'appliquer.", "success")
    else:
        flash("Reglages enregistres.", "success")
    return redirect(url_for("settings.reglages"))
