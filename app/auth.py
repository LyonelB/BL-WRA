"""
Authentification simple - un compte admin (ou plusieurs, meme role) avec
session Flask, dans l'esprit de auth.py sur bl-fmo. Pas de gestion de roles
pour cette v1 (facile a etendre plus tard si besoin).
"""

from functools import wraps

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import database

bp = Blueprint("auth", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = database.get_user_by_username(username)

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Identifiants incorrects.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            next_url = request.args.get("next") or url_for("dashboard.index")
            return redirect(next_url)

    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/compte", methods=["GET", "POST"])
@login_required
def compte():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        user = database.get_user(session["user_id"])

        if not check_password_hash(user["password_hash"], current):
            flash("Mot de passe actuel incorrect.", "error")
        elif len(new) < 8:
            flash("Le nouveau mot de passe doit faire au moins 8 caracteres.", "error")
        elif new != confirm:
            flash("La confirmation ne correspond pas.", "error")
        else:
            database.update_password(user["id"], generate_password_hash(new))
            flash("Mot de passe mis a jour.", "success")

    return render_template("compte.html")
