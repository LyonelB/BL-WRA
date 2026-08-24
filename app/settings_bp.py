"""
Reglages generaux (infos station), traitement audio et format de
conversion de la bibliotheque. La rotation des jingles vit desormais sur la
page Jingles (voir library.jingles_reglages) et la planification des pubs
sur la page Pubs (voir pub_slots.py).
"""

import logging
import subprocess
import threading

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

import database
import library_convert
import liquidsoap_client
from auth import login_required

bp = Blueprint("settings", __name__)

log = logging.getLogger("settings")

# Un seul worker gunicorn en prod (-w 1 --threads 4, voir app.py/radio-web.
# service) : un verrou en memoire de processus suffit a empecher deux
# conversions de bibliotheque de tourner en parallele.
_conversion_lock = threading.Lock()


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
        if form_name == "audio_format":
            return _save_audio_format()
        return _save_rotation()

    settings = database.get_all_settings()
    liquidsoap_status = liquidsoap_client.ping(current_app.config["LIQUIDSOAP_API_URL"])
    liquidsoap_since = _liquidsoap_service_since()
    relays = database.list_relays()
    crossfade_needs_restart = settings.get("audio_crossfade_pending_restart") == "1"

    convert_bitrate = settings.get("audio_convert_bitrate", "192")
    applied_bitrate = settings.get("audio_library_applied_bitrate")
    conversion_pending = applied_bitrate is not None and applied_bitrate != convert_bitrate
    conversion_running = database.get_state("library_convert_running", "0") == "1"

    return render_template(
        "reglages.html",
        settings=settings,
        liquidsoap_status=liquidsoap_status,
        liquidsoap_since=liquidsoap_since,
        relays=relays,
        crossfade_needs_restart=crossfade_needs_restart,
        conversion_pending=conversion_pending,
        conversion_running=conversion_running,
    )


@bp.route("/api/library-convert/status")
@login_required
def api_library_convert_status():
    """Etat de la conversion de bibliotheque en cours (JSON), interroge en
    JavaScript depuis Reglages -> Format audio pour afficher une
    progression en direct apres un clic sur "Relancer la conversion" (voir
    relancer_conversion ci-dessous), sans avoir a recharger la page."""
    settings = database.get_all_settings()
    convert_bitrate = settings.get("audio_convert_bitrate", "192")
    applied_bitrate = settings.get("audio_library_applied_bitrate")
    return jsonify({
        "running": database.get_state("library_convert_running", "0") == "1",
        "progress": database.get_state("library_convert_progress", ""),
        "summary": database.get_state("library_convert_summary", ""),
        "pending": applied_bitrate is not None and applied_bitrate != convert_bitrate,
    })


@bp.route("/reglages/format-audio/relancer", methods=["POST"])
@login_required
def relancer_conversion():
    """Relance en arriere-plan la conversion de toute la bibliotheque au
    bitrate actuellement configure (meme moteur que convert_library.py, voir
    library_convert.py). Necessaire quand le bitrate cible change apres
    coup : les fichiers deja importes ne se reconvertissent pas tout seuls."""
    if not _conversion_lock.acquire(blocking=False):
        flash("Une conversion de la bibliotheque est deja en cours.", "error")
        return redirect(url_for("settings.reglages"))

    try:
        bitrate = int(database.get_setting("audio_convert_bitrate", "192"))
    except (TypeError, ValueError):
        bitrate = 192

    app = current_app._get_current_object()

    def worker():
        try:
            _run_conversion_background(app, bitrate)
        finally:
            _conversion_lock.release()

    threading.Thread(target=worker, daemon=True, name="library-convert").start()
    flash(
        "Conversion de la bibliotheque lancee en arriere-plan "
        "(progression affichee ci-dessous, ca peut prendre quelques minutes).",
        "success",
    )
    return redirect(url_for("settings.reglages"))


def _run_conversion_background(app, bitrate_kbps):
    """Tourne dans un thread separe (voir relancer_conversion) : on ouvre
    notre propre contexte appli / connexion SQLite (celle de la requete qui
    a declenche le thread n'existe deja plus quand ce code s'execute)."""
    with app.app_context():
        db = database.get_db()
        database.set_state("library_convert_running", "1")
        database.set_state("library_convert_progress", "0/0")
        database.set_state("library_convert_summary", "")

        def on_progress(i, total):
            database.set_state("library_convert_progress", f"{i}/{total}")

        try:
            counts, total = library_convert.run(
                db, dry_run=False, bitrate_kbps=bitrate_kbps,
                on_line=log.info, on_progress=on_progress,
            )
            summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "aucun fichier"
            database.set_state("library_convert_summary", f"Terminee : {summary}.")
        except Exception:
            log.exception("Echec de la conversion de bibliotheque en arriere-plan")
            database.set_state("library_convert_summary", "Erreur pendant la conversion, voir les journaux (page Logs).")
        finally:
            database.set_state("library_convert_running", "0")


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


def _save_audio_format():
    """Bitrate cible pour la conversion de la bibliotheque (voir
    uploads.py/library_convert.py). Le format est fixe a mp3 pour l'instant
    (seul supporte par convert_to_mp3), pas de champ formulaire pour ca."""
    try:
        bitrate = int(request.form.get("audio_convert_bitrate", "192"))
    except (TypeError, ValueError):
        bitrate = 192
    bitrate = max(64, min(320, bitrate))  # bornes raisonnables pour du flux radio

    database.set_setting("audio_convert_format", "mp3")
    database.set_setting("audio_convert_bitrate", str(bitrate))
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
