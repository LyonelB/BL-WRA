"""
Endpoints appeles par Liquidsoap lui-meme (webhook on_track), pas par le
navigateur. Voir radio.liq / post_now_playing().
"""

from flask import Blueprint, current_app, jsonify, request

import rotation

bp = Blueprint("api", __name__)


@bp.route("/api/liquidsoap/on_track", methods=["POST"])
def on_track():
    token = current_app.config.get("WEBHOOK_TOKEN")
    if token:
        supplied = request.headers.get("X-Webhook-Token", "")
        if supplied != token:
            return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    rotation.handle_track_started(current_app._get_current_object(), data)
    return jsonify({"status": "ok"})
