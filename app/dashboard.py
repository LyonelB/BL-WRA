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

# Duree max (secondes) d'un flux SSE avant fermeture normale - voir
# _now_playing_events ci-dessous pour le pourquoi (incident du 28/08 :
# threads gunicorn epuises par des flux ouverts indefiniment).
_SSE_MAX_SECONDS = 600


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


def _now_playing_events(app):
    """Generateur commun aux flux /api/now-playing/stream (admin) et
    /api/public/now-playing/stream (public) : emet l'etat courant a chaque
    changement, sinon un keep-alive, toutes les 2 secondes.

    Borne a _SSE_MAX_SECONDS (10 min) plutot que "while True" indefiniment
    (comportement d'avant le 28/08) : un flux ouvert sans fin peut rester
    "connecte" cote OS bien apres que le client reel a disparu (onglet en
    veille, wifi coupe sans fermeture TCP propre, portable ferme...) - le
    thread gunicorn qui le sert reste alors bloque, potentiellement des
    dizaines de minutes (delai avant qu'une ecriture sur un socket mort
    finisse par echouer). Avec un seul worker/4 threads (voir
    radio-web.service), il suffit de quelques clients "fantomes" simultanes
    pour epuiser tous les threads disponibles et rendre toute l'appli
    injoignable, meme en local (incident constate le 28/08 : service actif
    mais plus aucune requete ne repondait). En fermant le flux normalement
    toutes les 10 minutes, le navigateur (EventSource, voir "retry:"
    ci-dessous) rouvre automatiquement une connexion neuve : un client mort
    est ainsi abandonne au plus tard au bout de 10 minutes au lieu de
    bloquer un thread indefiniment.
    """
    last_since = None
    # petit ping initial pour que le navigateur ouvre bien le flux
    yield "retry: 3000\n\n"
    started = time.monotonic()
    while time.monotonic() - started < _SSE_MAX_SECONDS:
        with app.app_context():
            snap = rotation.now_playing_snapshot()
        if snap["since"] != last_since:
            last_since = snap["since"]
            yield f"data: {json.dumps(snap)}\n\n"
        else:
            # keep-alive pour eviter les timeouts de proxy
            yield ": keep-alive\n\n"
        time.sleep(2)


@bp.route("/api/now-playing/stream")
@login_required
def now_playing_stream():
    app = current_app._get_current_object()
    return Response(_now_playing_events(app), mimetype="text/event-stream")


@bp.route("/api/skip", methods=["POST"])
@login_required
def skip():
    try:
        liquidsoap_client.skip(current_app.config["LIQUIDSOAP_API_URL"])
        flash("Titre suivant demandé.", "success")
    except liquidsoap_client.LiquidsoapUnavailable:
        flash("Liquidsoap ne répond pas.", "error")
    return redirect(url_for("dashboard.index"))


def _recent_musique_payload(limit=10):
    rows = database.recent_plays(limit=limit, category="musique")
    return [
        {
            "title": r["title"] or r["filename"],
            "artist": r["artist"] or "",
            "played_at": r["played_at"],
        }
        for r in rows
    ]


@bp.route("/public")
def public():
    """Page en lecture seule (pas de login), dans l'esprit du mode /public
    de bl-fmo : lecteur (lecture/volume), titre en cours et historique des
    10 derniers titres, sans acces a l'administration."""
    now_playing = rotation.now_playing_snapshot()
    station_name = database.get_setting("station_name", "Ma Webradio")
    stream_url = database.get_setting("stream_url", "")
    recent = _recent_musique_payload(limit=10)
    return render_template(
        "public.html",
        now_playing=now_playing,
        station_name=station_name,
        stream_url=stream_url,
        recent=recent,
    )


@bp.route("/api/public/recent")
def public_recent():
    """Historique des 10 derniers titres (musique uniquement), pour la mise
    a jour en direct de la page /public a chaque changement de titre (voir
    public.html : re-appele a chaque evenement du flux SSE, qui ne se
    declenche que sur un vrai changement de titre a l'antenne - pas sur les
    keep-alive)."""
    return jsonify({"recent": _recent_musique_payload(limit=10)})


@bp.route("/api/public/now-playing/stream")
def public_now_playing_stream():
    """Meme flux que /api/now-playing/stream mais sans authentification,
    pour alimenter la page /public (lecture seule)."""
    app = current_app._get_current_object()
    return Response(_now_playing_events(app), mimetype="text/event-stream")
