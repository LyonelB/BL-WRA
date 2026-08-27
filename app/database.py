"""
Acces SQLite - catalogue des titres/pubs/jingles, reglages, historique de
diffusion, comptes utilisateurs.

Pas d'ORM, comme dans bl-fmo/fm-monitor : une base SQLite simple, quelques
fonctions d'acces claires. Suffisant pour une webradio locale.
"""

import sqlite3
import time
from datetime import datetime

from flask import g, current_app

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL CHECK (category IN ('musique', 'jingle', 'pub')),
    filename TEXT NOT NULL,
    title TEXT,
    artist TEXT,
    duration REAL,
    active INTEGER NOT NULL DEFAULT 1,
    uploaded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tracks_category ON tracks(category);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rotation_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS play_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    filename TEXT NOT NULL,
    title TEXT,
    artist TEXT,
    played_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_play_log_played_at ON play_log(played_at);

CREATE TABLE IF NOT EXISTS relays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    mount TEXT NOT NULL,
    user TEXT NOT NULL DEFAULT 'source',
    password TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pub_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    -- Jours de diffusion, ex. "1,2,3,4,5" (1=lundi ... 7=dimanche). Toujours
    -- rempli (defaut = tous les jours) - voir _migrate_pub_slots_days pour
    -- les installations anterieures a ce champ.
    days TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7',
    active INTEGER NOT NULL DEFAULT 1,
    last_fired_date TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pub_slot_tracks (
    slot_id INTEGER NOT NULL REFERENCES pub_slots(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (slot_id, track_id)
);
CREATE INDEX IF NOT EXISTS idx_pub_slot_tracks_slot ON pub_slot_tracks(slot_id);

CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    -- Bornes de periode au format "MM-DD" (ex. "12-01"), repetees chaque
    -- annee - voir playlists.py/liquidsoap_client.write_playlists_file pour
    -- le calcul de start_key/end_key (comparaison numerique geree cote
    -- radio.liq, qui gere aussi le passage d'une annee sur l'autre).
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (playlist_id, track_id)
);
CREATE INDEX IF NOT EXISTS idx_playlist_tracks_playlist ON playlist_tracks(playlist_id);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DB_PATH"], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    """A appeler une fois au demarrage de l'appli (voir app.py)."""
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        _migrate_pub_slots_days(db)
        db.commit()
        _seed_defaults(app, db)


def _migrate_pub_slots_days(db):
    """Ajoute la colonne 'days' a pub_slots si elle n'existe pas encore
    (installations mises a jour depuis une version anterieure a cette
    fonctionnalite). "CREATE TABLE IF NOT EXISTS" ci-dessus ne touche pas
    aux tables deja existantes, d'ou cette migration explicite. Les
    creneaux existants recoivent tous les jours par defaut (valeur DEFAULT
    de la colonne), pour continuer a se declencher exactement comme avant
    (aucune restriction de jour au prealable)."""
    cols = {row["name"] for row in db.execute("PRAGMA table_info(pub_slots)").fetchall()}
    if "days" not in cols:
        db.execute(
            "ALTER TABLE pub_slots ADD COLUMN days TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7'"
        )


def _seed_defaults(app, db):
    from werkzeug.security import generate_password_hash

    for key, value in app.config["DEFAULT_SETTINGS"].items():
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    for key, value in (
        ("since_last_jingle", "0"),
        # Initialise a "maintenant" plutot qu'a 0 : sans ca, la toute
        # premiere musique diffusee apres une installation fraiche
        # declencherait systematiquement une pub immediate (l'intervalle
        # depuis "epoch 0" est toujours depasse).
        ("last_pub_played_at", str(time.time())),
    ):
        db.execute(
            "INSERT OR IGNORE INTO rotation_state (key, value) VALUES (?, ?)",
            (key, value),
        )

    # Compte admin par defaut si aucun compte n'existe encore
    row = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    if row["n"] == 0:
        default_user = app.config.get("DEFAULT_ADMIN_USER", "admin")
        default_pass = app.config.get("DEFAULT_ADMIN_PASSWORD", "changeme")
        db.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (default_user, generate_password_hash(default_pass), _now()),
        )
        app.logger.warning(
            "Aucun compte admin trouve : creation de '%s' avec le mot de passe "
            "par defaut. Changez-le immediatement depuis les reglages.",
            default_user,
        )

    db.commit()


def _now():
    # Heure locale (celle du serveur), pas UTC : coherente avec
    # rotation.py/pub_slots.py qui comparent deja aux creneaux horaires
    # avec datetime.now(), et avec l'affichage brut (played_at[11:19]) dans
    # le tableau de bord, qui ne fait aucune conversion de fuseau.
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Reglages (rotation, nom de station, etc.)
# --------------------------------------------------------------------------

def get_setting(key, default=None):
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_all_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    db.commit()


# --------------------------------------------------------------------------
# Etat interne de la rotation (compteurs), separe des reglages utilisateur
# --------------------------------------------------------------------------

def get_state(key, default=None):
    db = get_db()
    row = db.execute("SELECT value FROM rotation_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO rotation_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    db.commit()


# --------------------------------------------------------------------------
# Catalogue (musiques / jingles / pubs)
# --------------------------------------------------------------------------

def add_track(category, filename, title=None, artist=None, duration=None):
    db = get_db()
    cur = db.execute(
        "INSERT INTO tracks (category, filename, title, artist, duration, active, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (category, filename, title, artist, duration, _now()),
    )
    db.commit()
    return cur.lastrowid


def list_tracks(category, search=None, active_only=False):
    db = get_db()
    q = "SELECT * FROM tracks WHERE category = ?"
    params = [category]
    if active_only:
        q += " AND active = 1"
    if search:
        q += " AND (title LIKE ? OR artist LIKE ? OR filename LIKE ?)"
        like = f"%{search}%"
        params += [like, like, like]
    q += " ORDER BY uploaded_at DESC"
    return db.execute(q, params).fetchall()


def get_track(track_id):
    db = get_db()
    return db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


def delete_track(track_id):
    db = get_db()
    db.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    db.commit()


def set_track_active(track_id, active):
    db = get_db()
    db.execute("UPDATE tracks SET active = ? WHERE id = ?", (1 if active else 0, track_id))
    db.commit()


def update_track_metadata(track_id, title, artist):
    db = get_db()
    db.execute(
        "UPDATE tracks SET title = ?, artist = ? WHERE id = ?",
        (title or None, artist or None, track_id),
    )
    db.commit()


def count_tracks():
    db = get_db()
    rows = db.execute(
        "SELECT category, COUNT(*) AS n, SUM(active) AS actifs FROM tracks GROUP BY category"
    ).fetchall()
    return {r["category"]: {"total": r["n"], "actifs": r["actifs"] or 0} for r in rows}


def get_track_by_filename(category, filename):
    db = get_db()
    return db.execute(
        "SELECT * FROM tracks WHERE category = ? AND filename = ?",
        (category, filename),
    ).fetchone()


def random_active_track(category):
    db = get_db()
    return db.execute(
        "SELECT * FROM tracks WHERE category = ? AND active = 1 ORDER BY RANDOM() LIMIT 1",
        (category,),
    ).fetchone()


# --------------------------------------------------------------------------
# Historique de diffusion (pour le tableau de bord et d'eventuelles stats)
# --------------------------------------------------------------------------

def log_play(category, filename, title=None, artist=None):
    db = get_db()
    db.execute(
        "INSERT INTO play_log (category, filename, title, artist, played_at) VALUES (?, ?, ?, ?, ?)",
        (category, filename, title, artist, _now()),
    )
    db.commit()


def recent_plays(limit=25):
    db = get_db()
    return db.execute(
        "SELECT * FROM play_log ORDER BY played_at DESC LIMIT ?", (limit,)
    ).fetchall()


# --------------------------------------------------------------------------
# Comptes utilisateurs
# --------------------------------------------------------------------------

def get_user_by_username(username):
    db = get_db()
    return db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def get_user(user_id):
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def update_password(user_id, password_hash):
    db = get_db()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    db.commit()


# --------------------------------------------------------------------------
# Serveurs de diffusion externes (relais Icecast publics)
# --------------------------------------------------------------------------

def list_relays():
    db = get_db()
    return db.execute("SELECT * FROM relays ORDER BY id").fetchall()


def get_relay(relay_id):
    db = get_db()
    return db.execute("SELECT * FROM relays WHERE id = ?", (relay_id,)).fetchone()


def add_relay(name, host, port, mount, user, password):
    db = get_db()
    cur = db.execute(
        "INSERT INTO relays (name, host, port, mount, user, password, active) "
        "VALUES (?, ?, ?, ?, ?, ?, 1)",
        (name, host, port, mount, user or "source", password),
    )
    db.commit()
    return cur.lastrowid


def delete_relay(relay_id):
    db = get_db()
    db.execute("DELETE FROM relays WHERE id = ?", (relay_id,))
    db.commit()


def set_relay_active(relay_id, active):
    db = get_db()
    db.execute("UPDATE relays SET active = ? WHERE id = ?", (1 if active else 0, relay_id))
    db.commit()


# --------------------------------------------------------------------------
# Creneaux horaires de diffusion des pubs (Pubs -> Pubs planifiees).
# Remplace l'ancien reglage "une pub toutes les M minutes" : a l'heure du
# creneau (une fois par jour), les pubs cochees pour ce creneau sont
# poussees, l'une apres l'autre, a la fin du titre en cours.
# --------------------------------------------------------------------------

def list_pub_slots():
    """Tous les creneaux, avec leurs pubs assignees (actives ou non - pour
    que l'admin voie l'etat complet dans Reglages)."""
    db = get_db()
    slots = db.execute("SELECT * FROM pub_slots ORDER BY time").fetchall()
    result = []
    for slot in slots:
        tracks = db.execute(
            "SELECT t.* FROM pub_slot_tracks st JOIN tracks t ON t.id = st.track_id "
            "WHERE st.slot_id = ? ORDER BY st.position",
            (slot["id"],),
        ).fetchall()
        result.append({"slot": slot, "tracks": tracks})
    return result


def get_pub_slot(slot_id):
    db = get_db()
    return db.execute("SELECT * FROM pub_slots WHERE id = ?", (slot_id,)).fetchone()


def get_pub_slot_track_ids(slot_id):
    """Pour pre-cocher le formulaire de modification (toutes les pubs
    assignees, meme desactivees)."""
    db = get_db()
    rows = db.execute(
        "SELECT track_id FROM pub_slot_tracks WHERE slot_id = ? ORDER BY position",
        (slot_id,),
    ).fetchall()
    return [r["track_id"] for r in rows]


def get_pub_slot_tracks(slot_id):
    """Pour le declenchement reel : seulement les pubs encore actives."""
    db = get_db()
    return db.execute(
        "SELECT t.* FROM pub_slot_tracks st JOIN tracks t ON t.id = st.track_id "
        "WHERE st.slot_id = ? AND t.active = 1 ORDER BY st.position",
        (slot_id,),
    ).fetchall()


def list_scheduled_pub_track_ids():
    """Ensemble des id de pubs assignees a au moins un creneau actif (voir
    pub_slots.active) - utilise pour le badge "Planifiee" en lecture seule
    de la page Pubs (library.html). Contrairement a musiques/jingles, une
    pub n'a plus de bascule actif/inactif manuelle depuis l'interface :
    "planifiee" reflete uniquement son assignation reelle a un creneau, pas
    un champ a cocher a part (voir aussi get_pub_slot_tracks, meme logique
    au moment du declenchement reel)."""
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT st.track_id FROM pub_slot_tracks st "
        "JOIN pub_slots s ON s.id = st.slot_id WHERE s.active = 1"
    ).fetchall()
    return {r["track_id"] for r in rows}


def _set_pub_slot_tracks(db, slot_id, track_ids):
    db.execute("DELETE FROM pub_slot_tracks WHERE slot_id = ?", (slot_id,))
    for pos, track_id in enumerate(track_ids):
        db.execute(
            "INSERT INTO pub_slot_tracks (slot_id, track_id, position) VALUES (?, ?, ?)",
            (slot_id, track_id, pos),
        )


def add_pub_slot(time_str, days_str, track_ids):
    db = get_db()
    cur = db.execute(
        "INSERT INTO pub_slots (time, days, active, last_fired_date) VALUES (?, ?, 1, '')",
        (time_str, days_str),
    )
    slot_id = cur.lastrowid
    _set_pub_slot_tracks(db, slot_id, track_ids)
    db.commit()
    return slot_id


def update_pub_slot(slot_id, time_str, days_str, track_ids):
    db = get_db()
    db.execute(
        "UPDATE pub_slots SET time = ?, days = ? WHERE id = ?", (time_str, days_str, slot_id)
    )
    _set_pub_slot_tracks(db, slot_id, track_ids)
    db.commit()


def delete_pub_slot(slot_id):
    db = get_db()
    db.execute("DELETE FROM pub_slots WHERE id = ?", (slot_id,))
    db.commit()


def set_pub_slot_active(slot_id, active):
    db = get_db()
    db.execute("UPDATE pub_slots SET active = ? WHERE id = ?", (1 if active else 0, slot_id))
    db.commit()


def due_pub_slots(now_hm, today, weekday):
    """Creneaux actifs dont l'heure est deja passee (aujourd'hui), qui
    incluent le jour de la semaine courant (weekday : "1"=lundi ...
    "7"=dimanche, voir datetime.isoweekday()) parmi leurs jours de
    diffusion, et qui ne se sont pas encore declenches aujourd'hui.
    Le tour ',' || days || ',' LIKE '%,' || ? || ',%' evite tout faux
    positif de sous-chaine (non necessaire ici vu que "days" ne contient
    que des chiffres 1-7 uniques, mais plus sur si le format evolue)."""
    db = get_db()
    return db.execute(
        "SELECT * FROM pub_slots WHERE active = 1 AND time <= ? AND last_fired_date != ? "
        "AND (',' || days || ',') LIKE ('%,' || ? || ',%') "
        "ORDER BY time",
        (now_hm, today, weekday),
    ).fetchall()


def mark_pub_slot_fired(slot_id, today):
    db = get_db()
    db.execute("UPDATE pub_slots SET last_fired_date = ? WHERE id = ?", (today, slot_id))
    db.commit()


# --------------------------------------------------------------------------
# Playlists thematiques (Bibliotheque -> Playlists) : periodes programmees a
# l'avance (Noel, ete, braderie...) pendant lesquelles seules les musiques
# assignees a la playlist active sont diffusees, a la place de la
# bibliotheque complete. Voir playlists.py et radio.liq
# (active_playlist_files) pour la logique de selection.
# --------------------------------------------------------------------------

def list_playlists():
    """Toutes les playlists, dans l'ordre de creation - c'est aussi l'ordre
    de priorite utilise par radio.liq en cas de chevauchement entre deux
    playlists actives a la meme date (la plus ancienne gagne)."""
    db = get_db()
    playlists = db.execute("SELECT * FROM playlists ORDER BY id").fetchall()
    result = []
    for p in playlists:
        tracks = db.execute(
            "SELECT t.* FROM playlist_tracks pt JOIN tracks t ON t.id = pt.track_id "
            "WHERE pt.playlist_id = ? ORDER BY pt.position",
            (p["id"],),
        ).fetchall()
        result.append({"playlist": p, "tracks": tracks})
    return result


def get_playlist(playlist_id):
    db = get_db()
    return db.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()


def get_playlist_track_ids(playlist_id):
    """Pour pre-cocher le formulaire de modification."""
    db = get_db()
    rows = db.execute(
        "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    return [r["track_id"] for r in rows]


def _set_playlist_tracks(db, playlist_id, track_ids):
    db.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
    for pos, track_id in enumerate(track_ids):
        db.execute(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
            (playlist_id, track_id, pos),
        )


def add_playlist(name, start_date, end_date, track_ids):
    db = get_db()
    cur = db.execute(
        "INSERT INTO playlists (name, start_date, end_date, active, created_at) "
        "VALUES (?, ?, ?, 1, ?)",
        (name, start_date, end_date, _now()),
    )
    playlist_id = cur.lastrowid
    _set_playlist_tracks(db, playlist_id, track_ids)
    db.commit()
    return playlist_id


def update_playlist(playlist_id, name, start_date, end_date, track_ids):
    db = get_db()
    db.execute(
        "UPDATE playlists SET name = ?, start_date = ?, end_date = ? WHERE id = ?",
        (name, start_date, end_date, playlist_id),
    )
    _set_playlist_tracks(db, playlist_id, track_ids)
    db.commit()


def delete_playlist(playlist_id):
    db = get_db()
    db.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    db.commit()


def set_playlist_active(playlist_id, active):
    db = get_db()
    db.execute("UPDATE playlists SET active = ? WHERE id = ?", (1 if active else 0, playlist_id))
    db.commit()
