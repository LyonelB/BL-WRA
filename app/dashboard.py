"""
Tableau de bord "a l'antenne" : ce qui joue actuellement, historique recent,
bouton skip. Mise a jour en direct via Server-Sent Events, dans le meme
esprit que le dashboard temps reel de bl-fmo.
"""

import json
import time

from flask import Blueprint, Response, current_app, flash, jsonify, redirect, render_template, url_for

import database
import liquidsoap_client
import rotation
from auth import login_required

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@login_required
def index():
    counts = database.count_tracks()
    now_playing = rotation.now_playing_snapshot()
    recent = database.recent_plays(limit=15)
    settings = database.get_all_settings()
    liquidsoap_ok = liquidsoap_client.ping(current_app.config["LIQUIDSOAP_API_URL"]) is not None
    active_pub_slots = sum(1 for e in database.list_pub_slots() if e["slot"]["active"])
    return render_template(
        "dashboard.html",
        counts=counts,
        now_playing=now_playing,
        recent=recent,
        settings=settings,
        liquidsoap_ok=liquidsoap_ok,
        active_pub_slots=active_pub_slots,
    )


@bp.route("/api/now-playing")
@login_required
def now_playing_json():
    return jsonify(rotation.now_playing_snapshot())


@bp.route("/api/now-playing/stream")
@login_required
def now_playing_stream():
    app = current_app._get_current_object()

    def gen():
        last_since = None
        # petit ping initial pour que le navigateur ouvre bien le flux
        yield "retry: 3000\n\n"
        while True:
            with app.app_context():
                snap = rotation.now_playing_snapshot()
            if snap["since"] != last_since:
                last_since = snap["since"]
                yield f"data: {json.dumps(snap)}\n\n"
            else:
                # keep-alive pour eviter les timeouts de proxy
                yield ": keep-alive\n\n"
            time.sleep(2)

    return Response(gen(), mimetype="text/event-stream")


@bp.route("/api/skip", methods=["POST"])
@login_required
def skip():
    try:
        liquidsoap_client.skip(current_app.config["LIQUIDSOAP_API_URL"])
        flash("Titre suivant demandé.", "success")
    except liquidsoap_client.LiquidsoapUnavailable:
        flash("Liquidsoap ne répond pas.", "error")
    return redirect(url_for("dashboard.index"))


@bp.route("/public")
def public():
    """Page en lecture seule (pas de login), dans l'esprit du mode /public
    de bl-fmo : ce qui joue en ce moment, sans acces a l'administration."""
    now_playing = rotation.now_playing_snapshot()
    station_name = database.get_setting("station_name", "Ma Webradio")
    stream_url = database.get_setting("stream_url", "")
    return render_template(
        "public.html", now_playing=now_playing, station_name=station_name, stream_url=stream_url
    )


@bp.route("/api/public/now-playing/stream")
def public_now_playing_stream():
    """Meme flux que /api/now-playing/stream mais sans authentification,
    pour alimenter la page /public (lecture seule)."""
    app = current_app._get_current_object()

    def gen():
        last_since = None
        yield "retry: 3000\n\n"
        while True:
            with app.app_context():
                snap = rotation.now_playing_snapshot()
            if snap["since"] != last_since:
                last_since = snap["since"]
                yield f"data: {json.dumps(snap)}\n\n"
            else:
                yield ": keep-alive\n\n"
            time.sleep(2)

    return Response(gen(), mimetype="text/event-stream")
