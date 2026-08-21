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

# ---------------------------------------------------------------------------
# Securite : le serveur telnet et l'API harbor ne doivent ecouter qu'en local
# ---------------------------------------------------------------------------

settings.server.telnet.set(true)
settings.server.telnet.bind_addr.set("127.0.0.1")
settings.server.telnet.port.set(1234)

settings.harbor.bind_addrs.set(["127.0.0.1"])

# ---------------------------------------------------------------------------
# Bibliotheques : playlists rechargees automatiquement des qu'un fichier est
# ajoute/supprime par l'appli web (reload_mode="watch" -> inotify)
# ---------------------------------------------------------------------------

musiques_dir = "#{media_path}/musiques"
jingles_dir  = "#{media_path}/jingles"
pubs_dir     = "#{media_path}/pubs"

musiques_raw = playlist(id="musiques", mode="randomize", reload_mode="watch", musiques_dir)

# File d'attente prioritaire : c'est ici que l'appli web pousse un jingle ou
# une pub. Des qu'elle contient quelque chose, ca passe avant les musiques ;
# une fois vide, on retombe automatiquement sur les musiques.
prio = request.queue(id="prio")

# ---------------------------------------------------------------------------
# Traitement audio optionnel, active/desactivable depuis l'interface web
# (Reglages -> Traitement audio).
#
#   - normalize/blank_removal sont bascules "a chaud" (sans redemarrage) via
#     les references ci-dessous, modifiees par l'appli Flask via l'API HTTP
#     /audio-fx (voir plus bas) ; le changement prend effet au prochain
#     changement de titre (jamais au milieu d'un titre en cours).
#   - crossfade cree son propre "sous-horloge" interne a Liquidsoap
#     (bufferisation pour analyser le niveau sonore autour de chaque
#     transition) : il ne peut pas etre bascule a chaud sans redemarrage.
#     Son etat est donc lu une seule fois au demarrage depuis audio_fx.json,
#     comme "Diffusion externe" (relays.json) juste plus bas : changez-le
#     depuis Reglages puis `sudo systemctl restart liquidsoap-radio`.
#
# audio_fx.json est ecrit par l'appli Flask a chaque enregistrement des
# reglages, avec les 3 valeurs (meme celles bascultees a chaud) : ca permet
# de retrouver le dernier etat choisi si Liquidsoap redemarre independamment
# de l'appli web (mise a jour, reboot du serveur...).
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

enable_blank_removal = ref(audio_fx_initial.blank_removal)
enable_normalize     = ref(audio_fx_initial.normalize)

# Suppression des blancs : uniquement sur la bibliotheque de musiques (les
# jingles/pubs sont en general des fichiers courts et deja proprement
# coupes). max_blank/threshold sont volontairement prudents (silence de plus
# de 4s a moins de -40dB) pour ne pas rogner des passages juste calmes.
musiques_blank_stripped =
  blank.strip(id="musiques_blank_strip", max_blank=4., threshold=-40., musiques_raw)

musiques =
  switch(
    id="musiques_apres_blancs", track_sensitive=true,
    [(fun () -> !enable_blank_removal, musiques_blank_stripped),
     (fun () -> true, musiques_raw)]
  )

# ---------------------------------------------------------------------------
# Enchainement : priorite a la file (jingles/pubs), sinon musiques en continu
# ---------------------------------------------------------------------------

radio_sans_fondu = fallback(id="radio", track_sensitive=true, [prio, musiques])

radio =
  if audio_fx_initial.crossfade then
    crossfade(radio_sans_fondu)
  else
    radio_sans_fondu
  end

# Normalisation + compression/limiteur : chaine de mastering finale, apres le
# fondu enchaine. normalize() ramene le niveau RMS moyen a une cible
# constante (evite les ecarts de volume entre fichiers uploades) ; compress()
# avec un ratio eleve absorbe ensuite les pics restants, faisant office de
# limiteur. Reglages prudents par defaut ; ajustez ici si besoin (pas
# exposes dans l'interface, seule l'activation/desactivation l'est).
radio_avant_normalisation = radio

radio_normalise =
  compress(
    threshold=-6., ratio=4., attack=0.005, release=0.1, knee=3.,
    normalize(target=-16., radio_avant_normalisation)
  )

radio =
  switch(
    id="radio_apres_normalisation", track_sensitive=true,
    [(fun () -> !enable_normalize, radio_normalise),
     (fun () -> true, radio_avant_normalisation)]
  )

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
#   GET /audio-fx?name=normalize|blank_removal&value=true|false
#       -> active/desactive un traitement audio a la volee (voir la section
#          "Traitement audio optionnel" plus haut) ; pris en compte au
#          prochain changement de titre, aucun redemarrage necessaire.
#          (le crossfade ne peut pas etre bascule a chaud, voir plus haut)
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

def handle_audio_fx(hreq, hresp) =
  name = hreq.query["name"]
  value = hreq.query["value"] == "true"
  known = list.mem(name, ["normalize", "blank_removal"])
  if not known then
    hresp.status_code(400)
    hresp.json({error = "parametre 'name' invalide (normalize|blank_removal ; crossfade se change depuis audio_fx.json + redemarrage, voir Reglages)"})
  else
    if name == "normalize" then
      enable_normalize := value
    else
      enable_blank_removal := value
    end
    hresp.json({status = "ok", name = name, enabled = value})
  end
end

harbor.http.register(port=harbor_port, method="GET", "/audio-fx", handle_audio_fx)

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
