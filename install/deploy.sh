#!/usr/bin/env bash
# BL-WRA - deploiement d'une mise a jour depuis un clone git local vers
# /opt/radio (installation deja en place via install.sh).
#
# Usage habituel, depuis la racine du depot clone sur le serveur :
#   cd ~/BL-WRA
#   git pull
#   sudo ./install/deploy.sh
#
# Ce script remplace le cycle manuel "unzip + rsync + sed + chown +
# restart" utilise avant la publication sur GitHub. Il est idempotent :
# on peut le relancer sans risque apres chaque "git pull".
#
# Ce qu'il NE touche PAS (specifique a cette installation, jamais dans
# git) : /opt/radio/media/*.{mp3,wav,...} (bibliotheque reelle),
# /opt/radio/media/radio.db (base SQLite), RADIO_SECRET_KEY et le mot de
# passe admin (dans /etc/systemd/system/radio-web.service).
set -euo pipefail

RADIO_HOME="/opt/radio"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Ce script doit etre execute en root (sudo ./install/deploy.sh)." >&2
  exit 1
fi

if [ ! -d "$RADIO_HOME/app" ]; then
  echo "!! $RADIO_HOME/app introuvable : ce n'est pas une mise a jour," >&2
  echo "   utilisez d'abord install/install.sh pour une premiere installation." >&2
  exit 1
fi

echo "== Deploiement de $REPO_ROOT vers $RADIO_HOME =="

echo "== 1. Recuperation du mot de passe Icecast actuellement en prod =="
# radio.liq du depot contient un mot de passe placeholder
# ("changeme_icecast_source_password") ; on le remplace par celui deja en
# service pour ne jamais casser la connexion a Icecast lors d'une mise a
# jour (voir liquidsoap_password ci-dessous).
CURRENT_PW=""
if [ -f "$RADIO_HOME/liquidsoap/radio.liq" ]; then
  CURRENT_PW="$(sed -n 's/.*icecast_password = "\(.*\)".*/\1/p' "$RADIO_HOME/liquidsoap/radio.liq" | head -1)"
fi

echo "== 2. Appli web (app/) =="
rsync -a --exclude venv "$REPO_ROOT/app/" "$RADIO_HOME/app/"

echo "== 3. Liquidsoap (radio.liq) =="
# On ne redemarre liquidsoap-radio que si radio.liq change reellement :
# ce redemarrage coupe le flux quelques secondes (voir "Fiabilite 24h/24
# 7j/7" dans le README), inutile de l'infliger aux auditeurs pour une mise
# a jour qui ne touche que l'appli web (app/), comme une simple correction
# de texte. Hash calcule sur le fichier deploye, mot de passe Icecast
# reinjecte inclus, pour ne pas se declencher a tort sur une difference de
# mot de passe uniquement.
RADIO_LIQ="$RADIO_HOME/liquidsoap/radio.liq"
OLD_HASH=""
[ -f "$RADIO_LIQ" ] && OLD_HASH="$(sha256sum "$RADIO_LIQ" | cut -d' ' -f1)"

cp "$REPO_ROOT/liquidsoap/radio.liq" "$RADIO_LIQ"
if [ -n "$CURRENT_PW" ] && [ "$CURRENT_PW" != "changeme_icecast_source_password" ]; then
  sed -i "s/changeme_icecast_source_password/${CURRENT_PW}/" "$RADIO_LIQ"
  echo "Mot de passe Icecast existant reinjecte automatiquement."
else
  echo "!! Aucun mot de passe Icecast existant recupere : verifiez"
  echo "   $RADIO_LIQ avant de redemarrer liquidsoap-radio !"
fi

NEW_HASH="$(sha256sum "$RADIO_LIQ" | cut -d' ' -f1)"
if [ "$OLD_HASH" != "$NEW_HASH" ]; then
  RESTART_LIQUIDSOAP=1
else
  RESTART_LIQUIDSOAP=0
fi

echo "== 4. Permissions =="
chown -R radio:radio "$RADIO_HOME"

echo "== 5. Redemarrage des services =="
# radio-web : redemarrage systematique, sans impact sur le flux (juste
# l'interface d'administration indisponible une fraction de seconde).
systemctl restart radio-web

if [ "$RESTART_LIQUIDSOAP" -eq 1 ]; then
  echo "radio.liq a change : redemarrage de liquidsoap-radio (coupure du flux de quelques secondes)."
  systemctl restart liquidsoap-radio
else
  echo "radio.liq inchange : liquidsoap-radio non redemarre, flux non interrompu."
fi

echo "== Termine =="
sleep 1
systemctl status radio-web liquidsoap-radio --no-pager
