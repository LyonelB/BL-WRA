"""
Diffusion externe : liste des serveurs Icecast publics vers lesquels
relayer le flux (en plus de l'Icecast local defini dans radio.liq).

Geree depuis /reglages. A chaque changement, on reecrit un petit fichier
JSON (RELAYS_JSON_PATH) que radio.liq lit AU DEMARRAGE pour creer un
output.icecast supplementaire par serveur actif. Pas de rechargement a
chaud : apres un changement, Liquidsoap doit etre redemarre pour que ca
prenne effet (choix assume - voir README).
"""

import json
import os

from flask import Blueprint, current_app, flash, redirect, request, url_for

import database
from auth import login_required

bp = Blueprint("relays", __name__)


def write_relays_json(app):
    relays = database.list_relays()
    payload = [
        {
            "name": r["name"],
            "host": r["host"],
            "port": r["port"],
            "mount": r["mount"],
            "user": r["user"],
            "password": r["password"],
            "active": bool(r["active"]),
        }
        for r in relays
    ]
    path = app.config["RELAYS_JSON_PATH"]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    except OSError as exc:
        current_app.logger.warning("Impossible d'ecrire %s : %s", path, exc)


@bp.route("/reglages/relais/ajouter", methods=["POST"])
@login_required
def ajouter():
    name = request.form.get("name", "").strip() or "Serveur externe"
    host = request.form.get("host", "").strip()
    mount = request.form.get("mount", "").strip()
    user = request.form.get("user", "").strip() or "source"
    password = request.form.get("password", "").strip()

    try:
        port = int(request.form.get("port", "8000"))
    except ValueError:
        flash("Port invalide.", "error")
        return redirect(url_for("settings.reglages"))

    if not host or not mount or not password:
        flash("Hote, point de montage et mot de passe sont obligatoires.", "error")
        return redirect(url_for("settings.reglages"))

    database.add_relay(name, host, port, mount, user, password)
    write_relays_json(current_app._get_current_object())
    flash(
        "Serveur ajoute. Redemarrez Liquidsoap pour appliquer : "
        "sudo systemctl restart liquidsoap-radio",
        "success",
    )
    return redirect(url_for("settings.reglages"))


@bp.route("/reglages/relais/<int:relay_id>/supprimer", methods=["POST"])
@login_required
def supprimer(relay_id):
    database.delete_relay(relay_id)
    write_relays_json(current_app._get_current_object())
    flash(
        "Serveur supprime. Redemarrez Liquidsoap pour appliquer : "
        "sudo systemctl restart liquidsoap-radio",
        "success",
    )
    return redirect(url_for("settings.reglages"))


@bp.route("/reglages/relais/<int:relay_id>/activer", methods=["POST"])
@login_required
def activer(relay_id):
    relay = database.get_relay(relay_id)
    if relay:
        database.set_relay_active(relay_id, not relay["active"])
        write_relays_json(current_app._get_current_object())
        flash(
            "Redemarrez Liquidsoap pour appliquer : sudo systemctl restart liquidsoap-radio",
            "success",
        )
    return redirect(url_for("settings.reglages"))
