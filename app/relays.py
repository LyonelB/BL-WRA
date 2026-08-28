"""
Diffusion externe : liste des serveurs Icecast publics vers lesquels
relayer le flux (en plus de l'Icecast local defini dans radio.liq).

Geree depuis /reglages. A chaque changement, on reecrit un petit fichier
JSON (RELAYS_JSON_PATH) que radio.liq lit AU DEMARRAGE pour creer un
output.icecast supplementaire par serveur actif. Pas de rechargement a
chaud : la liste des relais n'est lue par Liquidsoap qu'a son demarrage,
donc chaque mutation (ajout/modification/suppression/activation)
redemarre automatiquement liquidsoap-radio ci-dessous pour l'appliquer
immediatement - pas besoin de lancer la commande a la main (voir
liquidsoap_client.restart_liquidsoap_service).
"""

import json
import os

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

import database
import liquidsoap_client
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


def _apply_relays_change(action_label):
    """Reecrit relays.json (voir write_relays_json) puis redemarre
    liquidsoap-radio immediatement, et flash un message adapte au resultat.
    "action_label" est au participe passe accorde au masculin singulier
    (ex. "Serveur ajouté", "Serveur modifié"...) : prefixe le message."""
    write_relays_json(current_app._get_current_object())
    ok, error = liquidsoap_client.restart_liquidsoap_service()
    if ok:
        flash(f"{action_label}. Liquidsoap redémarré.", "success")
    else:
        flash(
            f"{action_label}, mais le redémarrage automatique de Liquidsoap a échoué : {error}",
            "error",
        )


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
        flash("Hôte, point de montage et mot de passe sont obligatoires.", "error")
        return redirect(url_for("settings.reglages"))

    database.add_relay(name, host, port, mount, user, password)
    _apply_relays_change("Serveur ajouté")
    return redirect(url_for("settings.reglages"))


@bp.route("/reglages/relais/<int:relay_id>/modifier", methods=["GET", "POST"])
@login_required
def modifier(relay_id):
    relay = database.get_relay(relay_id)
    if not relay:
        flash("Serveur introuvable.", "error")
        return redirect(url_for("settings.reglages"))

    if request.method == "POST":
        name = request.form.get("name", "").strip() or "Serveur externe"
        host = request.form.get("host", "").strip()
        mount = request.form.get("mount", "").strip()
        user = request.form.get("user", "").strip() or "source"
        # Laisse vide : on garde le mot de passe actuel (voir
        # database.update_relay) - evite d'avoir a le retaper/le retrouver
        # pour un simple changement de nom.
        password = request.form.get("password", "").strip()

        try:
            port = int(request.form.get("port", "8000"))
        except ValueError:
            flash("Port invalide.", "error")
            return redirect(url_for("relays.modifier", relay_id=relay_id))

        if not host or not mount:
            flash("Hôte et point de montage sont obligatoires.", "error")
            return redirect(url_for("relays.modifier", relay_id=relay_id))

        database.update_relay(relay_id, name, host, port, mount, user, password or None)
        _apply_relays_change("Serveur modifié")
        return redirect(url_for("settings.reglages"))

    return render_template("relay_edit.html", relay=relay)


@bp.route("/reglages/relais/<int:relay_id>/supprimer", methods=["POST"])
@login_required
def supprimer(relay_id):
    database.delete_relay(relay_id)
    _apply_relays_change("Serveur supprimé")
    return redirect(url_for("settings.reglages"))


@bp.route("/reglages/relais/<int:relay_id>/activer", methods=["POST"])
@login_required
def activer(relay_id):
    relay = database.get_relay(relay_id)
    if relay:
        database.set_relay_active(relay_id, not relay["active"])
        _apply_relays_change("Serveur activé" if not relay["active"] else "Serveur désactivé")
    return redirect(url_for("settings.reglages"))
