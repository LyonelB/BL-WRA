"""
Point d'entree de l'appli web - assemble la config, la base de donnees et
les blueprints (dashboard, bibliotheque/jingles/pubs, reglages, api).

Lancement dev :   python app.py
Lancement prod :  gunicorn -w 1 --threads 4 -b 127.0.0.1:5000 app:app
(voir install/radio-web.service - un seul worker car l'etat de rotation et
le flux SSE sont geres en memoire/SQLite au sein d'un meme processus)
"""

import os

from flask import Flask

import config
import database
import liquidsoap_client
from auth import bp as auth_bp
from dashboard import bp as dashboard_bp
from library import bp as library_bp
from settings_bp import bp as settings_bp
from api import bp as api_bp
from relays import bp as relays_bp
from pub_slots import bp as pub_slots_bp
from logs_bp import bp as logs_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(config)

    # Dossiers de bibliotheque + base de donnees : on s'assure qu'ils existent
    for d in (config.MUSIQUES_DIR, config.JINGLES_DIR, config.PUBS_DIR):
        os.makedirs(d, exist_ok=True)

    app.teardown_appcontext(database.close_db)
    database.init_db(app)

    # Traitement audio (normalize/crossfade/blank_removal) et frequence
    # d'echantillonnage (voir liquidsoap/radio.liq) : au demarrage de
    # l'appli, on re-ecrit audio_fx.json/audio_format.json depuis les
    # reglages en base (au cas ou le fichier manque, ex. premiere mise a
    # jour depuis une version anterieure a ces fonctionnalites) et on
    # repousse a chaud ceux des traitements audio qui le permettent, au cas
    # ou Liquidsoap aurait ete redemarre independamment. Best-effort dans
    # tous les cas : ca ne doit jamais empecher l'appli de demarrer.
    with app.app_context():
        try:
            current_settings = database.get_all_settings()
            liquidsoap_client.write_audio_fx_file(
                app.config["AUDIO_FX_JSON_PATH"], current_settings
            )
            liquidsoap_client.write_audio_format_file(
                app.config["AUDIO_FORMAT_JSON_PATH"], current_settings
            )
            liquidsoap_client.sync_audio_fx(app.config["LIQUIDSOAP_API_URL"], current_settings)
        except OSError:
            pass

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(library_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(relays_bp)
    app.register_blueprint(pub_slots_bp)
    app.register_blueprint(logs_bp)

    @app.template_filter("duree")
    def duree_filter(seconds):
        if not seconds:
            return "--:--"
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"

    @app.template_filter("depuis")
    def depuis_filter(iso_ts):
        return iso_ts or ""

    _JOUR_LABELS = {"1": "Lun", "2": "Mar", "3": "Mer", "4": "Jeu", "5": "Ven", "6": "Sam", "7": "Dim"}

    @app.template_filter("jours_creneau")
    def jours_creneau_filter(days_str):
        """Affichage compact des jours de diffusion d'un creneau pub (voir
        pub_slots.py) : "Tous les jours" / "Semaine" / "Week-end" pour les
        cas courants, sinon la liste abregee (ex. "Lun, Mer, Ven")."""
        days = {d for d in (days_str or "").split(",") if d}
        if days == set(_JOUR_LABELS):
            return "Tous les jours"
        if days == {"1", "2", "3", "4", "5"}:
            return "Semaine"
        if days == {"6", "7"}:
            return "Week-end"
        ordered = sorted(days, key=lambda d: int(d) if d.isdigit() else 0)
        return ", ".join(_JOUR_LABELS.get(d, d) for d in ordered) or "-"

    @app.context_processor
    def inject_globals():
        return {"station_name": database.get_setting("station_name", "Ma Webradio")}

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
