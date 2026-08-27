#!/usr/bin/liquidsoap
# ============================================================================
# Teste et valide sur Liquidsoap 2.4.5 (image Docker officielle
# savonet/liquidsoap:v2.4.5 - voir install/liquidsoap-radio-docker.service,
# recommande sur Raspberry Pi a cause d'un bug connu du paquet apt sur
# Bookworm/ARM64). Avant toute mise en prod, verifiez toujours la syntaxe
# avec votre propre version :
#     liquidsoap --check radio.liq
#     liquidsoap --version
#
# Depuis Liquidsoap 2.3, les callbacks type on_track/on_metadata sont des
# methodes de la source qui prennent un parametre "synchronous" obligatoire
# (pas de thread.run separe a gerer). Si vous utilisez malgre tout le paquet
# apt en version 2.2.x (verifie fonctionnel sur 2.2.4, qui ne connait pas ce
# parametre), remplacez plus bas "on_track(synchronous=false, ...)" par
# "on_track(fun (m) -> thread.run(fast=false, {...}))".
# Pour toute version plus ancienne (1.x), consultez
# https://www.liquidsoap.info/doc-<votre-version>/ (menu "Reference").
# ============================================================================
# BL-WRA — moteur de diffusion Liquidsoap (webradio automatisee)
#
# Ce script :
#   - joue en boucle les musiques du dossier "musiques" en rotation aleatoire
#   - laisse l'appli web (Flask) inserer des jingles/pubs via une petite API
#     HTTP locale (harbor), et etre notifiee de chaque nouveau titre
#   - diffuse le resultat en Icecast (+ sortie carte son en option pour un
#     emetteur FM ou une sonorisation filaire)
#
# Toute la "logique d'administration" (combien de titres entre deux jingles,
# toutes les combien de minutes une pub, quels fichiers sont actifs, etc.)
# vit cote appli web, pas ici : ce script reste volontairement simple et
# stable, on ne le touche quasiment jamais une fois en prod.
# ============================================================================

log.level.set(3)

# ---------------------------------------------------------------------------
# Reglages a adapter a votre installation
# ---------------------------------------------------------------------------

# Dossier racine des bibliotheques (doit correspondre a MEDIA_ROOT dans
# l'appli Flask, cf. app/config.py)
media_path = "/opt/radio/media"

# URL locale de l'appli web Flask (pour la notifier des changements de titre)
webapp_url = "http://127.0.0.1:5000"

# Port de la petite API HTTP exposee par Liquidsoap pour que Flask puisse
# pousser un jingle/une pub ou passer au titre suivant (localhost uniquement)
harbor_port = 8001

# Icecast : a adapter avec vos identifiants reels
icecast_host = "localhost"
icecast_port = 8000
icecast_password = "changeme_icecast_source_password"
icecast_mount = "radio.mp3"

# Fichier lu une seule fois au demarrage pour l'etat initial des 3 bascules
# de traitement audio (voir section "Traitement audio optionnel" plus bas et
# Reglages -> Traitement audio cote appli web). Ecrit par l'appli Flask a
# chaque enregistrement des reglages.
audio_fx_path = "/opt/radio/liquidsoap/audio_fx.json"

# Fichier lu une seule fois au demarrage pour la frequence d'echantillonnage
# du pipeline audio (voir Reglages -> Format audio de la bibliotheque cote
# appli web). DOIT rester synchronise avec la frequence de conversion
# choisie la-bas (uploads.py/library_convert.py) : c'est justement
# l'uniformite qui evite a Liquidsoap de reechantillonner a la volee a
# chaque transition (cause identifiee des avertissements "Latency is too
# high"). Ecrit par l'appli Flask a chaque enregistrement de ce reglage ;
# necessite un redemarrage de Liquidsoap pour s'appliquer, comme crossfade.
audio_format_path = "/opt/radio/liquidsoap/audio_format.json"

# Fichier RELU A CHAQUE selection d'une nouvelle musique (pas seulement au
# demarrage) pour la fenetre de non-repetition (voir Bibliotheque ->
# Rotation cote appli web, et musique_next_request plus bas). Un changement
# ici prend donc effet immediatement, sans redemarrer Liquidsoap : c'est un
# parametre de selection, pas une caracteristique du graphe audio construite
# une seule fois comme audio_fx/audio_format ci-dessus.
musique_rotation_path = "/opt/radio/liquidsoap/musique_rotation.json"

# Fichier RELU A CHAQUE selection d'une nouvelle musique (comme
# musique_rotation_path ci-dessus) : playlists thematiques programmees a
# l'avance (Noel, ete, braderie...), voir /playlists cote appli web et
# active_playlist_files plus bas. Ecrit par l'appli Flask a chaque
# creation/modification/suppression/activation d'une playlist.
playlists_path = "/opt/radio/liquidsoap/playlists.json"

# ---------------------------------------------------------------------------
# Securite : le serveur telnet et l'API harbor ne doivent ecouter qu'en local
# ---------------------------------------------------------------------------

settings.server.telnet.set(true)
settings.server.telnet.bind_addr.set("127.0.0.1")
settings.server.telnet.port.set(1234)

settings.harbor.bind_addrs.set(["127.0.0.1"])

# ---------------------------------------------------------------------------
# Frequence d'echantillonnage du pipeline audio : DOIT etre definie avant la
# creation de toute source (playlists, request.queue...) ci-dessous, sinon
# elle n'a aucun effet. 44100Hz ou 48000Hz uniquement (les deux seules
# frequences valides pour l'encodage MP3 en sortie, voir uploads.py).
# ---------------------------------------------------------------------------

def load_audio_format() =
  default = {sample_rate = 44100}
  if file.exists(audio_format_path) then
    try
      let json.parse (parsed : {sample_rate: int}) = file.contents(audio_format_path)
      parsed
    catch _ : [error.json] do
      log(label="audio_format", "audio_format.json invalide, valeur par defaut utilisee")
      default
    end
  else
    default
  end
end

audio_format_initial = load_audio_format()

settings.frame.audio.samplerate.set(audio_format_initial.sample_rate)

# ---------------------------------------------------------------------------
# Bibliotheques : playlists rechargees automatiquement des qu'un fichier est
# ajoute/supprime par l'appli web (reload_mode="watch" -> inotify)
# ---------------------------------------------------------------------------

musiques_dir = "#{media_path}/musiques"
jingles_dir  = "#{media_path}/jingles"
pubs_dir     = "#{media_path}/pubs"

# ---------------------------------------------------------------------------
# Musiques : selection avec anti-repetition (26/08). Remplace un simple
# playlist(mode="randomize") qui pouvait rejouer le meme titre a quelques
# minutes d'intervalle (repetition constatee en prod). request.dynamic
# appelle musique_next_request() a chaque fois qu'un nouveau fichier est
# necessaire ; on y exclut les titres joues recemment (fenetre reglable
# depuis Bibliotheque -> Rotation, cote appli web) avant de tirer au hasard.
#
# Le dossier est relu (file.ls) a CHAQUE selection plutot qu'en mode "watch"
# comme avant : c'est plus simple et au moins aussi a jour (un fichier tout
# juste ajoute est immediatement candidat), et le cout est negligeable vu la
# frequence des selections (quelques dizaines de fichiers, une fois toutes
# les quelques minutes).
#
# Vigilance memoire (cf. HISTORIQUE 21/08 plus bas sur normalize/
# blank_removal) : musique_history ne grossit jamais indefiniment - purge a
# chaque appel de tout ce qui depasse musique_history_max_seconds (6h), quel
# que soit le reglage de fenetre choisi cote appli web.
# ---------------------------------------------------------------------------

def load_musique_no_repeat_minutes() =
  default = 120
  if file.exists(musique_rotation_path) then
    try
      let json.parse (parsed : {no_repeat_minutes: int}) = file.contents(musique_rotation_path)
      parsed.no_repeat_minutes
    catch _ : [error.json] do
      log(label="musique_rotation", "musique_rotation.json invalide, valeur par defaut utilisee")
      default
    end
  else
    default
  end
end

# Liste de (chemin, timestamp) des musiques recemment jouees.
musique_history = ref([])
musique_history_max_seconds = 6. * 60. * 60.

# ---------------------------------------------------------------------------
# Playlists thematiques (27/08) : une periode programmee a l'avance (dates
# "JJ/MM", repetees chaque annee) pendant laquelle seules les musiques
# assignees a cette playlist sont diffusees, a la place de la bibliotheque
# complete. playlists.json est ecrit cote Flask avec, pour chaque playlist,
# start_key/end_key deja precalcules (ex. "12-25" -> 1225) : la comparaison
# numerique ci-dessous gere nativement le passage d'une annee sur l'autre
# (ex. Noel : 1er decembre -> 6 janvier, start_key=1201 > end_key=106).
# ---------------------------------------------------------------------------

def load_playlists() =
  default = []
  if file.exists(playlists_path) then
    try
      let json.parse (parsed : {playlists: [{id: int, name: string, start_date: string,
        end_date: string, start_key: int, end_key: int, active: bool,
        track_paths: [string]}]}) = file.contents(playlists_path)
      parsed.playlists
    catch _ : [error.json] do
      log(label="playlists", "playlists.json invalide, bibliotheque complete utilisee")
      default
    end
  else
    default
  end
end

# Fichiers de la playlist thematique active aujourd'hui, s'il y en a une, ou
# null() sinon (bibliotheque complete utilisee dans ce cas). En cas de
# chevauchement entre plusieurs playlists actives a la meme date, la
# premiere dans l'ordre de creation (ordre du tableau JSON) est prioritaire.
# Les fichiers qui n'existent plus (musique supprimee depuis) sont ecartes
# par securite plutot que de faire planter la selection.
def active_playlist_files() =
  today = time.local(time())
  today_key = today.month * 100 + today.day
  matches = list.filter(fun (p) ->
      p.active and (
        if p.start_key <= p.end_key then
          today_key >= p.start_key and today_key <= p.end_key
        else
          today_key >= p.start_key or today_key <= p.end_key
        end
      ),
    load_playlists()
  )
  if list.length(matches) == 0 then
    null()
  else
    chosen = list.nth(matches, 0)
    existing = list.filter(fun (f) -> file.exists(f), chosen.track_paths)
    if list.length(existing) == 0 then null() else existing end
  end
end

def musique_next_request() =
  no_repeat_seconds = float_of_int(load_musique_no_repeat_minutes()) * 60.
  now = time()

  # Purge independante du reglage courant, pour borner la memoire meme si
  # quelqu'un choisit une fenetre tres longue cote appli web.
  musique_history := list.filter(fun (e) -> now - snd(e) < musique_history_max_seconds, musique_history())

  recent = list.filter(fun (e) -> now - snd(e) < no_repeat_seconds, musique_history())
  recent_files = list.map(fun (e) -> fst(e), recent)

  # Playlist thematique active aujourd'hui si elle existe, sinon bibliotheque
  # complete (comportement inchange par rapport a avant l'ajout des
  # playlists).
  all_files = null.default(active_playlist_files(), fun () -> file.ls(absolute=true, musiques_dir))
  candidates = list.filter(fun (f) -> not list.mem(f, recent_files), all_files)
  # Bibliotheque trop petite pour la fenetre choisie : on retombe sur la
  # liste complete plutot que de bloquer la diffusion.
  pool = if list.length(candidates) > 0 then candidates else all_files end

  if list.length(pool) == 0 then
    # Dossier musiques vide : rien a jouer pour l'instant.
    null()
  else
    idx = random.int(min=0, max=list.length(pool))
    fallback_pick = list.nth(pool, 0)
    picked = list.nth(default=fallback_pick, pool, idx)
    musique_history := list.add((picked, now), musique_history())
    request.create(picked)
  end
end

musiques_raw = request.dynamic(id="musiques", musique_next_request)

# File d'attente prioritaire : c'est ici que l'appli web pousse un jingle ou
# une pub. Des qu'elle contient quelque chose, ca passe avant les musiques ;
# une fois vide, on retombe automatiquement sur les musiques.
prio = request.queue(id="prio")

# ---------------------------------------------------------------------------
# Traitement audio optionnel, active/desactivable depuis l'interface web
# (Reglages -> Traitement audio) : normalisation de volume + compression/
# limiteur, fondu enchaine (crossfade), suppression des blancs.
#
# HISTORIQUE (21/08) : normalize/blank_removal avaient ete retires
# temporairement du graphe audio suite a une fuite memoire constatee en
# production (croissance ~75 Mo/minute) peu apres leur activation. La cause
# exacte n'a jamais ete confirmee, mais le mecanisme utilise a l'epoque pour
# les basculer A CHAUD (un switch() entre version traitee/non traitee du
# flux, pilote par l'API harbor /audio-fx) etait suspecte. Reintroduits ici
# le 24/08 (a la demande explicite, hors production) avec un changement
# deliberement conservateur : plus AUCUN des 3 traitements n'est bascule a
# chaud. Comme crossfade avant eux, les 3 sont maintenant construits UNE
# SEULE FOIS a la creation du graphe (simples "if", pas de switch()) et
# necessitent donc un redemarrage de Liquidsoap pour s'appliquer apres un
# changement dans Reglages (bouton "Redemarrer Liquidsoap maintenant").
# C'est un compromis assume : moins pratique qu'une bascule instantanee,
# mais on evite de re-introduire le mecanisme suspecte sans savoir s'il
# etait vraiment en cause. Cote interface web, chaque traitement affiche un
# avertissement d'usage CPU (voir reglages.html) - normalize et
# blank_removal analysent en continu le flux (fenetre glissante), donc plus
# couteux qu'un simple fondu.
#
# audio_fx.json est ecrit par l'appli Flask a chaque enregistrement des
# reglages (voir settings_bp.py/liquidsoap_client.py) ; ce fichier est relu
# ici une seule fois au demarrage, comme "Diffusion externe" (relays.json)
# juste plus bas.
# ---------------------------------------------------------------------------

def load_audio_fx() =
  default = {normalize = false, crossfade = true, blank_removal = false}
  if file.exists(audio_fx_path) then
    try
      let json.parse (
        parsed : {normalize: bool, crossfade: bool, blank_removal: bool}
      ) = file.contents(audio_fx_path)
      parsed
    catch _ : [error.json] do
      log(label="audio_fx", "audio_fx.json invalide, valeurs par defaut utilisees")
      default
    end
  else
    default
  end
end

audio_fx_initial = load_audio_fx()

# ---------------------------------------------------------------------------
# Enchainement : priorite a la file (jingles/pubs), sinon musiques en continu
# ---------------------------------------------------------------------------

radio_sans_fondu = fallback(id="radio", track_sensitive=true, [prio, musiques_raw])

# Ordre : suppression des blancs d'abord (nettoie le flux brut), puis fondu
# enchaine (transitions), puis normalisation/compression en dernier (agit
# sur le niveau du flux final, apres toute manipulation de transition).
radio = radio_sans_fondu

radio =
  if audio_fx_initial.blank_removal then
    blank.strip(radio)
  else
    radio
  end

radio =
  if audio_fx_initial.crossfade then
    crossfade(radio)
  else
    radio
  end

radio =
  if audio_fx_initial.normalize then
    normalize(compress.exponential(radio))
  else
    radio
  end

radio = mksafe(radio)

# ---------------------------------------------------------------------------
# Notifier l'appli web a chaque nouveau titre diffuse.
# Cote Flask, ce webhook sert a la fois au tableau de bord "a l'antenne"
# (SSE) et a la logique de rotation (compter les titres, declencher un
# jingle/une pub au bon moment via l'API /push ci-dessous).
#
# On attache on_track DIRECTEMENT sur "musiques" et sur "prio" (pas sur
# "radio" apres crossfade/mksafe) : la categorie est ainsi connue avec
# certitude (pas besoin de la deviner depuis le chemin du fichier, qui ne
# survit pas forcement au passage dans crossfade), et on evite les
# notifications fantomes que crossfade peut generer en interne.
# La categorie des jingles/pubs pousses via /push est recuperee depuis
# l'annotation "category" ajoutee a la volee par handle_push ci-dessous.
# ---------------------------------------------------------------------------

def post_now_playing(category, m) =
  payload =
    json.stringify({
      category = category,
      title = m["title"],
      artist = m["artist"],
      filename = m["filename"]
    })
  response =
    http.post(
      "#{webapp_url}/api/liquidsoap/on_track",
      headers=[("Content-Type", "application/json")],
      data=payload
    )
  if response.status_code != 200 then
    log(label="webapp", "Erreur en notifiant l'appli web (#{response.status_code})")
  end
end

musiques_raw.on_track(synchronous=false, fun (m) -> post_now_playing("musique", m))
prio.on_track(synchronous=false, fun (m) -> post_now_playing(m["category"], m))

# ---------------------------------------------------------------------------
# API HTTP locale pilotee par l'appli Flask (localhost uniquement, cf.
# settings.harbor.bind_addrs plus haut).
#
#   GET /push?file=/opt/radio/media/jingles/xxx.mp3&type=jingle
#       -> insere ce fichier en tete de la file prioritaire. "type" (jingle
#          ou pub) est annote sur la requete pour que la notification
#          on_track sache de quoi il s'agit (voir post_now_playing).
#
#   GET /skip
#       -> passe immediatement au titre suivant
#
# (il n'y a volontairement plus d'endpoint /audio-fx pour basculer un
# traitement audio a chaud - voir la section "Traitement audio optionnel"
# plus haut : les 3 traitements necessitent maintenant un redemarrage)
# ---------------------------------------------------------------------------

# NB : les parametres sont nommes hreq/hresp (et non request/response) pour
# ne pas masquer le module global "request" (request.create, ...) dont on a
# besoin a l'interieur de handle_push.

def handle_push(hreq, hresp) =
  file = hreq.query["file"]
  cat = hreq.query["type"]
  cat = if cat == "" then "inconnu" else cat end
  if file == "" then
    hresp.status_code(400)
    hresp.json({error = "parametre 'file' manquant"})
  else
    rid = request.create("annotate:category=\"#{cat}\":#{file}")
    ignore(prio.push(rid))
    hresp.json({status = "ok", file = file, type = cat})
  end
end

harbor.http.register(port=harbor_port, method="GET", "/push", handle_push)

def handle_skip(hreq, hresp) =
  ignore(radio.skip())
  hresp.json({status = "ok"})
end

harbor.http.register(port=harbor_port, method="GET", "/skip", handle_skip)

def handle_ping(hreq, hresp) =
  hresp.json({status = "ok", source = "radio.liq"})
end

harbor.http.register(port=harbor_port, method="GET", "/ping", handle_ping)

# ---------------------------------------------------------------------------
# Sortie(s)
#
# NB : name/description ci-dessous sont les metadonnees envoyees a Icecast
# (visibles sur la page de statut Icecast et par certains lecteurs/logiciels
# de tuner). C'est independant du "nom de la station" reglable depuis
# l'interface web (Reglages), qui lui ne concerne que l'affichage dans
# l'appli - personnalisez les deux si besoin.
# ---------------------------------------------------------------------------

output.icecast(
  %mp3(bitrate=128),
  host=icecast_host,
  port=icecast_port,
  password=icecast_password,
  mount=icecast_mount,
  name="Ma Webradio",
  description="Webradio automatisee (musiques, jingles, pubs) - BL-WRA",
  genre="Various",
  radio
)

# Sortie carte son locale, a decommenter si vous alimentez un emetteur FM
# ou une sonorisation filaire directement depuis ce serveur :
# output.alsa(radio)

# ---------------------------------------------------------------------------
# Diffusion externe : un output.icecast supplementaire par serveur actif
# defini depuis l'interface web (Reglages -> Diffusion externe). Le fichier
# est ecrit par l'appli Flask ; lu une seule fois au demarrage (redemarrez
# liquidsoap-radio apres un changement pour l'appliquer).
# ---------------------------------------------------------------------------

relays_path = "/opt/radio/liquidsoap/relays.json"

def load_relays() =
  if file.exists(relays_path) then
    try
      let json.parse (
        parsed : [{name: string, host: string, port: int, mount: string,
                   user: string, password: string, active: bool}]
      ) = file.contents(relays_path)
      parsed
    catch _ : [error.json] do
      log(label="relays", "relays.json invalide, ignore")
      []
    end
  else
    []
  end
end

list.iter(
  fun (r) ->
    if r.active then
      output.icecast(
        %mp3(bitrate=128),
        host=r.host,
        port=r.port,
        password=r.password,
        mount=r.mount,
        user=r.user,
        name=r.name,
        radio
      )
    end,
  load_relays()
)
