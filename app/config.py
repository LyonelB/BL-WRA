"""
Configuration centrale de l'appli web.

Reprend le principe de bl-fmo (fm-monitor) : un seul fichier de config lu au
demarrage, valeurs surchargeables par variables d'environnement pour ne pas
avoir a toucher au code en prod.
"""

import os

# Racine de l'appli (dossier app/)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Doit correspondre a "media_path" dans liquidsoap/radio.liq
MEDIA_ROOT = os.environ.get("RADIO_MEDIA_ROOT", "/opt/radio/media")
MUSIQUES_DIR = os.path.join(MEDIA_ROOT, "musiques")
JINGLES_DIR = os.path.join(MEDIA_ROOT, "jingles")
PUBS_DIR = os.path.join(MEDIA_ROOT, "pubs")

# Base SQLite (catalogue, reglages, historique de diffusion, comptes)
DB_PATH = os.environ.get("RADIO_DB_PATH", os.path.join(MEDIA_ROOT, "radio.db"))

# Fichier lu par radio.liq au demarrage pour connaitre les serveurs Icecast
# externes vers lesquels relayer le flux (voir /reglages, section "Diffusion
# externe"). Un redemarrage de liquidsoap-radio est necessaire pour prendre
# en compte un changement ici (pas de rechargement a chaud).
RELAYS_JSON_PATH = os.environ.get("RADIO_RELAYS_JSON", "/opt/radio/liquidsoap/relays.json")

# Fichier lu par radio.liq au demarrage pour l'etat initial des 3 bascules de
# traitement audio (voir /reglages, section "Traitement audio") : normalize,
# crossfade, blank_removal. Les 3 sont construites une seule fois au
# demarrage du graphe audio (aucune n'est bascultable a chaud - voir le
# commentaire "Traitement audio optionnel" dans radio.liq pour le pourquoi),
# donc necessitent `systemctl restart liquidsoap-radio` pour s'appliquer
# apres un changement dans Reglages (bouton "Redemarrer Liquidsoap
# maintenant").
AUDIO_FX_JSON_PATH = os.environ.get("RADIO_AUDIO_FX_JSON", "/opt/radio/liquidsoap/audio_fx.json")

# Fichier lu par radio.liq au demarrage pour la frequence d'echantillonnage
# du pipeline audio (voir /reglages, section "Format audio de la
# bibliotheque" et settings.frame.audio.samplerate.set dans radio.liq).
# Necessite aussi un redemarrage de Liquidsoap pour s'appliquer.
AUDIO_FORMAT_JSON_PATH = os.environ.get("RADIO_AUDIO_FORMAT_JSON", "/opt/radio/liquidsoap/audio_format.json")

# Fichier RELU EN CONTINU par radio.liq (pas seulement au demarrage) a
# chaque selection d'une nouvelle musique, pour la fenetre de
# non-repetition (voir /bibliotheque, reglage "musique_no_repeat_minutes").
# Contrairement a audio_fx.json/audio_format.json, un changement ici prend
# effet immediatement, sans redemarrer Liquidsoap : c'est un parametre de
# selection, pas une caracteristique du graphe audio construite une seule
# fois. Ecrit par l'appli Flask a chaque enregistrement du reglage (voir
# library.py/liquidsoap_client.py).
MUSIQUE_ROTATION_JSON_PATH = os.environ.get(
    "RADIO_MUSIQUE_ROTATION_JSON", "/opt/radio/liquidsoap/musique_rotation.json"
)

# Cle de session Flask - a definir via variable d'environnement en prod
SECRET_KEY = os.environ.get("RADIO_SECRET_KEY", "change-moi-en-production")

# Petite API HTTP exposee par Liquidsoap (harbor), voir liquidsoap/radio.liq
LIQUIDSOAP_API_URL = os.environ.get("LIQUIDSOAP_API_URL", "http://127.0.0.1:8001")

# Jeton partage optionnel pour verifier que /api/liquidsoap/on_track vient
# bien de notre script Liquidsoap (appel local uniquement par defaut, mais
# ca ne coute rien de le proteger un minimum).
WEBHOOK_TOKEN = os.environ.get("RADIO_WEBHOOK_TOKEN", "")

# Extensions audio acceptees a l'upload
ALLOWED_EXTENSIONS = {"mp3", "ogg", "flac", "wav", "m4a", "aac"}
MAX_UPLOAD_MB = int(os.environ.get("RADIO_MAX_UPLOAD_MB", "100"))

# Valeurs par defaut des reglages de rotation (modifiables ensuite depuis
# l'interface web, stockees dans la table settings)
DEFAULT_SETTINGS = {
    # un jingle toutes les N musiques
    "jingle_every_n_titles": "4",
    # affichage du bouton "Jouer maintenant" sur les pages Jingles/Pubs -
    # desactivable independamment sur chaque page pour eviter les fausses
    # manips (voir library.py, _list_view/_play_now_view)
    "jingle_play_now_enabled": "1",
    "pub_play_now_enabled": "1",
    # Anti-repetition des musiques : un meme titre ne peut pas repasser
    # avant ce nombre de minutes (voir liquidsoap_client.py et radio.liq,
    # musique_next_request). 0 desactive la contrainte (retour au tirage
    # completement aleatoire).
    "musique_no_repeat_minutes": "120",
    # les pubs sont programmees a heure fixe, voir la table pub_slots et
    # Reglages -> Pubs planifiees (plus de reglage d'intervalle ici)
    # nombre de secondes de fondu enchaine entre les titres (info only,
    # le crossfade reel est regle dans radio.liq)
    "crossfade_seconds": "3",
    # nom de la station affiche dans l'interface (modifiable depuis Reglages)
    "station_name": "Ma Webradio",
    # URL publique du flux Icecast, utilisee pour le lecteur audio integre
    # (ex: http://192.168.1.123:8000/radio.mp3) - a renseigner depuis Reglages
    "stream_url": "",
    # Traitement audio optionnel (voir liquidsoap/radio.liq), bascules
    # individuelles depuis Reglages -> Traitement audio.
    # "1"/"0" plutot que bool : coherent avec le reste de la table settings
    # (cle/valeur texte, cf. database.set_setting).
    "audio_normalize_enabled": "0",
    "audio_crossfade_enabled": "1",
    "audio_blank_removal_enabled": "0",
    # "1" tant qu'un changement de traitement audio (normalize, crossfade ou
    # blank_removal) n'a pas ete applique par un redemarrage de Liquidsoap
    # (voir bouton "Redemarrer Liquidsoap maintenant" dans Reglages ->
    # Traitement audio). Remplace l'ancienne cle
    # "audio_crossfade_pending_restart" (crossfade etait alors le seul des
    # 3 a necessiter un redemarrage).
    "audio_fx_pending_restart": "0",
    # Format cible pour la normalisation de la bibliotheque, reglable depuis
    # Reglages -> Format audio (voir uploads.py pour le "pourquoi"). Le
    # format est fixe a mp3 pour l'instant (seul supporte par
    # uploads.convert_to_mp3) ; bitrate et frequence sont eux reellement
    # configurables. Seules 44100/48000Hz sont proposees (96000Hz n'est pas
    # un format MP3 valide, voir uploads.py).
    "audio_convert_format": "mp3",
    "audio_convert_bitrate": "192",
    "audio_convert_sample_rate": "44100",
    # "1" tant qu'un changement de frequence n'a pas ete applique par un
    # redemarrage de Liquidsoap (settings.frame.audio.samplerate.set n'est
    # lu qu'au demarrage, voir radio.liq).
    "audio_sample_rate_pending_restart": "0",
    # Dernier bitrate/frequence reellement appliques a l'ensemble de la
    # bibliotheque (mis a jour par library_convert.run, CLI ou bouton web) :
    # permet a Reglages de detecter un ecart avec les valeurs cibles
    # ci-dessus et d'afficher le bouton "Relancer la conversion" seulement
    # si necessaire. Initialises aux memes valeurs par defaut que les
    # reglages cibles pour qu'une installation neuve (rien encore importe)
    # n'affiche pas le bouton inutilement.
    "audio_library_applied_bitrate": "192",
    "audio_library_applied_sample_rate": "44100",
}
