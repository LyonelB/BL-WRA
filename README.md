# BL-WRA — interface web pour piloter une webradio Liquidsoap

**BL-WRA** (**W**eb**R**adio **A**dmin, dans le même esprit que
[BL-FMO](https://github.com/LyonelB/fm-monitor) « Fm MOnitor » et BL-DMO
« Dab+ MOnitor ») est une interface d'administration pour une webradio
Liquidsoap : bibliothèque de musiques, jingles et pubs, insérés
automatiquement selon des règles de rotation réglables — pas de grille de
programmes, juste un enchaînement en boucle avec rotation aléatoire des
musiques, des jingles réguliers et des pubs programmées à heure fixe.

Construit dans le même esprit que **BL-FMO** (fm-monitor) : Flask + SQLite,
tableau de bord temps réel via Server-Sent Events, webhooks, déploiement
systemd, reverse-proxy nginx, accès local (mDNS type `radio.local`).

Copyright (C) 2026 Lyonel Bernard. Licence : [GPL-3.0](LICENSE) — libre à
vous de l'installer, la modifier et la redistribuer, à condition que vos
versions modifiées et redistribuées restent elles aussi sous licence
GPL-3.0.

## Installation depuis GitHub

```bash
git clone https://github.com/LyonelB/bl-wra.git
cd bl-wra/install
sudo ./install.sh
```

(remplacez l'URL par celle de votre propre fork/dépôt si vous republiez ce
projet ailleurs). Voir la section [Installation](#installation) plus bas
pour la suite des étapes, et
[Installation sur Raspberry Pi](#installation-sur-raspberry-pi-4-ou-5) pour
les spécificités ARM64.

## Hypothèses prises pour cette v1

Faute de précisions sur l'hébergement final, ce paquet est pensé pour être
installé sur **n'importe quel serveur Linux** (Raspberry Pi ou VPS), et
suppose une diffusion **Icecast** (flux réseau écouté par des
récepteurs/enceintes/applis) — Liquidsoap peut aussi alimenter en
parallèle une carte son locale (sortie `output.alsa`, ligne commentée dans
`radio.liq`) si vous pilotez un émetteur FM ou une sonorisation filaire
depuis la même machine. Un seul compte administrateur est prévu au
démarrage ; il est trivial d'en ajouter d'autres (voir `database.py`,
table `users`) si plusieurs personnes doivent gérer le contenu.
Ajustez ces points selon votre installation réelle. Le nom de la station
(affiché dans l'interface et sur le flux Icecast) se configure depuis
"Réglages" après l'installation — aucune personnalisation du code n'est
nécessaire pour l'adapter à votre radio.

## Architecture

```
┌─────────────────┐   fichiers déposés    ┌──────────────────────┐
│   Interface web  │ ─────────────────────▶│  liquidsoap/radio.liq │
│   (Flask + SQLite)│                       │  - playlist musiques  │
│                  │◀── webhook on_track ───│    (rotation auto)    │
│                  │                       │  - file prioritaire   │
│                  │──── push jingle/pub ──▶│    (jingles/pubs)     │
└─────────────────┘   (API HTTP locale)    │  - sortie Icecast     │
                                            └──────────────────────┘
```

- **Liquidsoap** (`liquidsoap/radio.liq`) reste volontairement simple et
  stable : il joue les musiques du dossier `media/musiques` en boucle
  aléatoire, et laisse une "file prioritaire" vide en attente. Il expose une
  petite API HTTP locale (port 8001, 127.0.0.1 uniquement) pour que l'appli
  web puisse y déposer un jingle/une pub, et notifie l'appli web (webhook
  HTTP) à chaque nouveau titre diffusé.
- **L'appli web** (`app/`) gère tout le reste : upload et catalogue des
  musiques/jingles/pubs, règles de rotation ("un jingle toutes les N
  musiques", des pubs programmées à heure fixe via des "créneaux"), tableau
  de bord "à l'antenne" en direct, bouton "titre suivant", page publique en
  lecture seule (`/public`).
- Toute la logique de rotation vit côté Python (`app/rotation.py`) : on peut
  changer les réglages depuis l'interface sans jamais toucher au script
  Liquidsoap ni redémarrer la diffusion.

## Dossiers de médias

```
media/
  musiques/   <- déposés/gérés via "Bibliothèque"
  jingles/    <- déposés/gérés via "Jingles"
  pubs/       <- déposés/gérés via "Pubs"
  radio.db    <- base SQLite (catalogue, réglages, historique, comptes)
```

Chaque dossier est surveillé par Liquidsoap (`reload_mode="watch"`) : dès
qu'un fichier est ajouté ou supprimé via l'interface, la playlist
correspondante se met à jour automatiquement, sans redémarrage.

## Format audio de la bibliothèque

Le pipeline audio de Liquidsoap tourne à une fréquence d'échantillonnage
fixe (44100Hz ou 48000Hz, réglable — voir plus bas). Une bibliothèque
mélangeant des formats différents (MP3 44100Hz, MP3 48000Hz, WAV non
compressé...) oblige Liquidsoap à décoder/rééchantillonner à la volée à
chaque transition vers un fichier d'un format différent — identifié comme
cause probable des avertissements `Latency is too high` observés en prod.

Pour éviter ça, tout fichier importé depuis l'interface (Bibliothèque,
Jingles, Pubs) est désormais automatiquement converti en **MP3 stéréo, sans
pochette**, à la fréquence et au bitrate configurés depuis Réglages ->
Format audio (défauts : 192kbps/44100Hz — voir `app/uploads.py`,
`convert_to_mp3`). Nécessite `ffmpeg` sur le serveur (installé par
`install/install.sh` ; pour une installation existante :
`sudo apt-get install -y ffmpeg`). Si `ffmpeg` est absent ou que la
conversion échoue pour un fichier donné, celui-ci est conservé dans son
format d'origine plutôt que de bloquer l'import — l'appli reste utilisable,
mais ce fichier ne bénéficie pas de l'uniformisation.

**Fréquence d'échantillonnage** : seules 44100Hz et 48000Hz sont proposées
(les deux seules valides pour l'encodage MP3 utilisé ici — 96kHz, par
exemple, n'est pas un format MP3 valide). Contrairement au bitrate,
applicable immédiatement, changer la fréquence doit aussi resynchroniser le
pipeline Liquidsoap lui-même (`settings.frame.audio.samplerate.set` dans
`radio.liq`, relu uniquement au démarrage) : un bouton **« Redémarrer
Liquidsoap maintenant »** apparaît dans Réglages dès que ce champ change.
Après le redémarrage, relancez aussi la conversion de la bibliothèque (voir
ci-dessous) pour que les fichiers déjà importés suivent la nouvelle
fréquence.

**Bibliothèque déjà en ligne avant ce changement** : un script de
maintenance ponctuel, `app/convert_library.py`, convertit tous les fichiers
existants au même format et met à jour la base en conséquence. À exécuter
une fois sur le serveur, avec l'utilisateur `radio` (important : pas en
root, sinon l'appli web ne pourrait plus gérer ensuite les fichiers
convertis) :

```bash
cd /opt/radio/app
sudo -u radio venv/bin/python3 convert_library.py --dry-run   # aperçu, aucune modification
sudo -u radio venv/bin/python3 convert_library.py             # exécution
```

Le script est idempotent (relançable sans risque, il ignore ce qui est
déjà au bon format) et sans danger pour le flux en cours : un fichier
remplacé pendant qu'il est en cours de lecture par Liquidsoap continue de
jouer normalement (comportement standard de Linux), seule sa prochaine
lecture utilisera la version convertie.

## Installation

Voir `install/install.sh` (Debian / Raspberry Pi OS). En résumé :

1. `sudo ./install/install.sh` — installe Liquidsoap, Icecast2, Python,
   copie l'appli dans `/opt/radio`, crée les services systemd.
2. Éditez `/opt/radio/liquidsoap/radio.liq` : mot de passe Icecast, nom de
   station.
3. Éditez `/etc/systemd/system/radio-web.service` : changez
   `RADIO_SECRET_KEY`.
4. `systemctl restart liquidsoap-radio radio-web`
5. Ouvrez `http://<ip-du-serveur>:5000`, connectez-vous avec
   `admin` / `changeme`, puis **changez immédiatement ce mot de passe**
   dans "Mon compte".
6. (Optionnel) configurez `install/nginx-radio.conf` pour exposer
   l'interface sur le port 80/443 plutôt que directement sur le port 5000.

### En développement (sans systemd)

```bash
cd app
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export RADIO_MEDIA_ROOT=$(pwd)/../media
python app.py
# puis, dans un autre terminal, avec liquidsoap installé :
liquidsoap ../liquidsoap/radio.liq
```

## Réglages de rotation

- **Anti-répétition des musiques** (onglet "Bibliothèque", section
  "Rotation") — un même titre ne peut pas repasser avant N minutes (120 par
  défaut, réglable, 0 pour désactiver). Si la bibliothèque est trop petite
  pour respecter ce délai, la contrainte est ignorée ponctuellement plutôt
  que d'interrompre la diffusion. Contrairement aux réglages de "Traitement
  audio" ci-dessous, celui-ci s'applique immédiatement, sans redémarrer
  Liquidsoap (lu à chaque sélection d'un titre, voir `radio.liq`).
- **Playlists thématiques** (onglet "Playlists") — une période programmée à
  l'avance (ex. Noël du 1/12 au 6/1, été du 1/6 au 31/8) pendant laquelle
  seules les musiques assignées à cette playlist sont diffusées, à la place
  de la bibliothèque complète ; celle-ci reprend automatiquement une fois la
  période terminée, sans intervention. Les dates ("JJ/MM") se répètent
  chaque année. La contrainte anti-répétition ci-dessus continue de
  s'appliquer parmi les titres de la playlist active. En cas de
  chevauchement entre deux playlists actives à la même date, la plus
  ancienne (créée en premier) est prioritaire. Comme le réglage précédent,
  lu à chaque sélection d'un titre : aucun redémarrage de Liquidsoap requis,
  y compris pour le changement de jour qui active/désactive une playlist.
- **Un jingle toutes les N musiques** (onglet "Jingles") — compteur remis à
  zéro à chaque jingle inséré. Mettre 0 pour désactiver.
- **Pubs planifiées (créneaux horaires)** — section dédiée dans "Réglages" :
  pour chaque créneau (une heure, ex. `10:30`), on coche la ou les pubs à
  diffuser. À l'heure dite, dès la fin du titre en cours (Liquidsoap
  n'insère jamais une pub au milieu d'une musique), les pubs cochées
  passent les unes après les autres. Un créneau ne se déclenche qu'une
  fois par jour ; s'il n'y a aucune pub cochée, il ne se passe rien mais
  le créneau est bien marqué "passé" pour la journée. Si le serveur était
  arrêté à l'heure d'un créneau, il se déclenche au prochain titre diffusé
  après le redémarrage. Priorité à la pub sur le jingle : si un créneau se
  déclenche à une transition donnée, le jingle éventuellement dû attendra
  la transition suivante.

Vous pouvez toujours forcer un jingle ou une pub précis immédiatement avec
le bouton **« Jouer maintenant »** dans les onglets Jingles/Pubs, sans
attendre le prochain déclenchement automatique.

## Traitement audio

Toujours depuis "Réglages", trois traitements optionnels appliqués par
Liquidsoap sur l'ensemble du flux, chacun activable/désactivable
indépendamment :

- **Enchaînement avec fondu (fade in / fade out)** — plutôt qu'un
  enchaînement sec entre les titres. Charge CPU légère.
- **Normalisation de volume + compression/limiteur** — égalise le niveau
  sonore entre les titres. Charge CPU modérée (analyse en continu).
- **Détection et suppression des blancs** — coupe les silences en début/fin
  de piste. Charge CPU modérée (analyse en continu).

**Historique** : normalize et blank_removal avaient été retirés
temporairement le 21/08 suite à une fuite mémoire constatée en production
peu après leur activation (cause jamais confirmée avec certitude, mais le
mécanisme de bascule à chaud utilisé à l'époque était suspecté).
Réintroduits le 24/08 avec un changement délibérément conservateur : **les
3 traitements nécessitent désormais un redémarrage de Liquidsoap pour
s'appliquer** (plus aucun n'est basculé à chaud, contrairement à l'ancien
mécanisme suspecté). Un bouton **« Redémarrer Liquidsoap maintenant »**
apparaît dans l'interface dès qu'un réglage a changé (pas besoin de SSH).
Il nécessite que `install/radio-sudoers` soit installé (`sudo ./install.sh`
le fait automatiquement sur une nouvelle installation ; sur une
installation existante, copiez-le une fois à la main — voir le commentaire
en tête du fichier). Recommandé : n'activer qu'un traitement à la fois lors
d'un premier test, et surveiller la mémoire (`free -h`, ou
`systemctl status liquidsoap-radio`) dans les minutes/heures qui suivent.

Les 3 réglages sont sauvegardés dans `liquidsoap/audio_fx.json` (côté
serveur, hors dépôt) afin que Liquidsoap retrouve le bon état s'il redémarre
indépendamment de l'appli web.

## Fiabilité 24h/24 7j/7

Objectif du projet : fonctionnement continu sans intervention manuelle.
Plusieurs mécanismes y contribuent :

- **`Restart=on-failure`** sur `liquidsoap-radio.service` et
  `radio-web.service` : si l'un des deux plante vraiment (crash), systemd le
  relance automatiquement en quelques secondes.
- **Redémarrage préventif quotidien** de `liquidsoap-radio`
  (`liquidsoap-radio-restart.timer`, 4h du matin par défaut) : une dérive
  mémoire/CPU qui dégrade le flux (audible en continu) sans jamais faire
  planter le processus n'est *pas* rattrapée par `Restart=on-failure` — ce
  timer repart d'une base saine chaque nuit, indépendamment de la cause
  exacte. Coupure du flux limitée à quelques secondes, à l'heure creuse.
  Changez l'heure dans `/etc/systemd/system/liquidsoap-radio-restart.timer`
  (`OnCalendar=`) puis `sudo systemctl daemon-reload && sudo systemctl
  restart liquidsoap-radio-restart.timer`. Activable/désactivable directement
  depuis Réglages -> Fiabilité (nécessite le sudo dédié, voir
  `install/radio-sudoers` — une installation existante mise à jour via
  `deploy.sh` doit recopier ce fichier une fois : `sudo cp
  install/radio-sudoers /etc/sudoers.d/radio-wra && sudo chmod 0440
  /etc/sudoers.d/radio-wra`).
- **`deploy.sh` ne redémarre `liquidsoap-radio` que si `radio.liq` a
  réellement changé** (comparaison de hash avant/après copie, mot de passe
  Icecast réinjecté inclus) : une mise à jour qui ne touche que l'appli web
  (`app/`) — un correctif d'interface, une page en plus, etc. — redémarre
  seulement `radio-web` (aucun impact sur le flux) sans couper l'antenne
  pour rien.

## Page Logs

Menu "Logs" dans l'interface : affiche les 100 dernières lignes de journal
(`journalctl`) de `radio-web` et `liquidsoap-radio`, avec un sélecteur pour
filtrer sur l'un des deux services, coloration ERROR/WARNING/INFO et
rafraîchissement automatique toutes les 30 secondes — même principe que la
page Stats de **BL-FMO** (route `/api/logs`).

Pour que ça fonctionne, l'utilisateur système `radio` (celui qui fait
tourner `radio-web.service`) doit être membre du groupe `systemd-journal`,
sinon `journalctl` ne renvoie rien (la page l'indique clairement plutôt que
de planter). `install.sh` le fait automatiquement sur une nouvelle
installation ; sur une installation existante, exécutez une fois :

```bash
sudo usermod -aG systemd-journal radio
sudo systemctl restart radio-web
```

## Sécurité — à faire avant mise en production

- Changez le mot de passe admin par défaut (`admin` / `changeme`).
- Changez `RADIO_SECRET_KEY` (clé de session Flask).
- Changez le mot de passe source Icecast dans `radio.liq`.
- Le serveur telnet Liquidsoap et l'API harbor n'écoutent que sur
  `127.0.0.1` — ne les exposez pas sur le réseau.
- Si l'appli web est accessible au-delà du réseau local, passez par nginx
  en HTTPS (certbot) plutôt qu'en HTTP direct.

## Installation sur Raspberry Pi (4 ou 5)

`install/install.sh` est pensé pour ça : il installe les paquets, génère
automatiquement les mots de passe Icecast et la clé secrète Flask (plus
besoin de les éditer à la main), les injecte dans `radio.liq` et dans le
service `radio-web`, puis démarre tout via systemd. L'interface est ensuite
joignable depuis n'importe quel appareil du réseau local, sans nginx
nécessaire pour démarrer : `http://<nom-du-pi>.local:5000`.

### Bug connu : Liquidsoap segfault sur Raspberry Pi OS Bookworm (ARM64)

Le paquet Liquidsoap fourni par Debian/Raspberry Pi OS (2.1.x/2.2.x selon la
version) a des plantages connus (segmentation fault) sur ARM64 après
certaines mises à jour de bibliothèques (ffmpeg/pulseaudio) — voir
[savonet/liquidsoap#3491](https://github.com/savonet/liquidsoap/issues/3491).
Confirmé en pratique sur Raspberry Pi OS Bookworm : `liquidsoap-radio.service`
crashe avec `signal=SEGV`. `install.sh` teste `liquidsoap --version` juste
après l'installation et vous prévient si ça plante.

**Solution recommandée : image Docker officielle**, qui n'a pas ce problème.
Le dépôt fournit directement `install/liquidsoap-radio-docker.service`,
testé avec `savonet/liquidsoap:v2.4.5` :

```bash
sudo apt-get install -y docker.io
sudo systemctl enable --now docker

sudo systemctl stop liquidsoap-radio
sudo systemctl disable liquidsoap-radio

sudo chmod -R o+rX /opt/radio/media /opt/radio/liquidsoap
sudo cp install/liquidsoap-radio-docker.service /etc/systemd/system/liquidsoap-radio.service
sudo systemctl daemon-reload

sudo docker pull savonet/liquidsoap:v2.4.5
sudo docker run --rm -v /opt/radio/liquidsoap:/opt/radio/liquidsoap:ro \
  savonet/liquidsoap:v2.4.5 --check /opt/radio/liquidsoap/radio.liq

sudo systemctl enable --now liquidsoap-radio
```

`--network host` est utilisé pour que le conteneur parle directement à
Flask (`127.0.0.1:5000`) et à Icecast (`localhost:8000`) sans mapping de
ports.

## Tester / dépanner le script Liquidsoap

Avant de le mettre en service, vérifiez que la syntaxe correspond bien à la
version installée sur votre serveur :

```bash
liquidsoap --check liquidsoap/radio.liq
# ou, via Docker :
docker run --rm -v $(pwd)/liquidsoap:/opt/radio/liquidsoap:ro \
  savonet/liquidsoap:v2.4.5 --check /opt/radio/liquidsoap/radio.liq
```

Ce script est validé sur **Liquidsoap 2.4.5** (image Docker recommandée) et
sur **2.2.4** (paquet apt Debian/Ubuntu classique — mais évitez-le sur
Raspberry Pi ARM64 à cause du bug ci-dessus). Le seul point qui diffère
entre ces deux versions est le paramètre de `thread.run` : `synchronous=false`
sur 2.4.x, `fast=false` sur 2.2.x (voir le commentaire en tête de
`radio.liq`). Pour toute version plus ancienne (1.x), consultez la
[référence officielle](https://www.liquidsoap.info/) de votre version —
`harbor.http.register` et `request.queue` ont aussi changé entre 1.x et 2.x.
Pour déboguer en direct, le serveur telnet reste disponible en local :

```bash
telnet 127.0.0.1 1234
help
prio.push /opt/radio/media/jingles/xxx.mp3
```

## Limites connues de cette v1

- Pas de gestion de rôles (un seul type de compte) — facile à étendre côté
  `database.py`/`auth.py` si besoin plus tard.
- La rotation automatique n'insère qu'un seul élément (jingle *ou* pub) par
  transition de musique, pour rester prévisible.
- Le tableau de bord utilise du "polling" SSE toutes les 2 secondes plutôt
  qu'un vrai push instantané — largement suffisant pour ce cas d'usage et
  plus simple à déployer de façon fiable derrière un reverse-proxy.

