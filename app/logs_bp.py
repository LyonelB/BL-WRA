"""
Page "Logs" - affiche les dernieres lignes de journal (journalctl) des
services radio-web et liquidsoap-radio directement dans l'interface, dans
le meme esprit que la page Stats de BL-FMO (route /api/logs, meme
principe : journalctl -u <service> -n 100 --no-pager, affiche cote client
dans un encart sombre avec coloration ERROR/WARNING/INFO).

Necessite que l'utilisateur systeme "radio" (celui qui fait tourner
radio-web.service) soit dans le groupe systemd-journal pour pouvoir lire le
journal sans etre root - voir install.sh et le README (section "Page
Logs"). Sans cette permission, journalctl renvoie simplement une sortie
vide (pas une erreur bloquante) : /api/logs le detecte et l'affiche comme
tel plutot que de planter.
"""

import subprocess

from flask import Blueprint, jsonify, render_template, request

from auth import login_required

bp = Blueprint("logs", __name__)

# Cle du selecteur cote page -> arguments "-u" journalctl correspondants.
UNITS = {
    "tous": ["radio-web.service", "liquidsoap-radio.service"],
    "web": ["radio-web.service"],
    "liquidsoap": ["liquidsoap-radio.service"],
}
NB_LIGNES = 100


@bp.route("/logs")
@login_required
def logs_page():
    return render_template("logs.html")


@bp.route("/api/logs")
@login_required
def api_logs():
    unit_key = request.args.get("unit", "tous")
    units = UNITS.get(unit_key, UNITS["tous"])

    cmd = ["journalctl"]
    for unit in units:
        cmd += ["-u", unit]
    cmd += ["-n", str(NB_LIGNES), "--no-pager"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return jsonify({"logs": "", "error": "journalctl introuvable sur ce serveur."})
    except subprocess.TimeoutExpired:
        return jsonify({"logs": "", "error": "La lecture des logs a pris trop de temps (timeout)."})

    if result.returncode != 0:
        return jsonify({
            "logs": "",
            "error": (result.stderr or f"journalctl a renvoye le code {result.returncode}").strip()
            + " - l'utilisateur 'radio' est-il bien dans le groupe systemd-journal ?",
        })

    if not result.stdout.strip():
        return jsonify({
            "logs": "",
            "error": "Aucune ligne renvoyee. Verifiez que l'utilisateur 'radio' est bien dans le "
            "groupe systemd-journal (voir README, section 'Page Logs') et que radio-web a ete "
            "redemarre depuis.",
        })

    return jsonify({"logs": result.stdout})
