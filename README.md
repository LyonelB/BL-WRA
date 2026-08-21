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

Depuis l'onglet "Réglages" :

- **Un jingle toutes les N musiques** — compteur remis à zéro à chaque
  jingle inséré. Mettre 0 pour désactiver.
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
  enchaînement sec entre les titres. Historiquement toujours actif ; c'est
  le seul des trois réglages qui **nécessite un redémarrage de Liquidsoap**
  pour s'appliquer (`sudo systemctl restart liquidsoap-radio`) : le fondu
  enchaîné a besoin de mettre en place son propre système de bufferisation
  au démarrage du flux, il ne peut pas être basculé à la volée.
- **Normalisation de volume + compression/limiteur** — égalise le niveau
  sonore moyen entre les fichiers uploadés (normalisation), puis absorbe les
  pics restants (compression à ratio élevé, en pratique un limiteur).
  S'applique dès l'enregistrement, sans redémarrage.
- **Détection et suppression des blancs** — coupe les silences de plus de 4
  secondes à l'intérieur des musiques (pas les jingles/pubs, généralement
  déjà propres). S'applique dès l'enregistrement, sans redémarrage.

Les deux derniers prennent effet au prochain changement de titre (l'appli
web pousse l'état vers l'API locale de Liquidsoap) ; ils sont aussi
sauvegardés dans `liquidsoap/audio_fx.json` (côté serveur, hors dépôt) afin
que Liquidsoap retrouve le bon état s'il redémarre indépendamment de l'appli
web. Les réglages fins (seuils, ratio, durée du fondu...) ne sont pas
exposés dans l'interface ; ajustez-les directement dans `radio.liq` si
besoin (section "Traitement audio optionnel").

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
