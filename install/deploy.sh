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
cp "$REPO_ROOT/liquidsoap/radio.liq" "$RADIO_HOME/liquidsoap/radio.liq"
if [ -n "$CURRENT_PW" ] && [ "$CURRENT_PW" != "changeme_icecast_source_password" ]; then
  sed -i "s/changeme_icecast_source_password/${CURRENT_PW}/" "$RADIO_HOME/liquidsoap/radio.liq"
  echo "Mot de passe Icecast existant reinjecte automatiquement."
else
  echo "!! Aucun mot de passe Icecast existant recupere : verifiez"
  echo "   $RADIO_HOME/liquidsoap/radio.liq avant de redemarrer liquidsoap-radio !"
fi

echo "== 4. Permissions =="
chown -R radio:radio "$RADIO_HOME"

echo "== 5. Redemarrage des services =="
systemctl restart radio-web
systemctl restart liquidsoap-radio

echo "== Termine =="
sleep 1
systemctl status radio-web liquidsoap-radio --no-pager
