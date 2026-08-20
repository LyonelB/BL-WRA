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
from auth import bp as auth_bp
from dashboard import bp as dashboard_bp
from library import bp as library_bp
from settings_bp import bp as settings_bp
from api import bp as api_bp
from relays import bp as relays_bp
from pub_slots import bp as pub_slots_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(config)

    # Dossiers de bibliotheque + base de donnees : on s'assure qu'ils existent
    for d in (config.MUSIQUES_DIR, config.JINGLES_DIR, config.PUBS_DIR):
        os.makedirs(d, exist_ok=True)

    app.teardown_appcontext(database.close_db)
    database.init_db(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(library_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(relays_bp)
    app.register_blueprint(pub_slots_bp)

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

    @app.context_processor
    def inject_globals():
        return {"station_name": database.get_setting("station_name", "Ma Webradio")}

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
