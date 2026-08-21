#!/usr/bin/env bash
# BL-WRA - Installation sur Debian / Raspberry Pi OS (Bookworm ou plus
# recent). A executer en root (sudo ./install.sh) depuis le dossier
# "install/" du depot clone (ou de l'archive decompressee).
set -euo pipefail

RADIO_HOME="/opt/radio"

echo "== 1. Paquets systeme =="
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y liquidsoap python3 python3-venv python3-pip nginx openssl avahi-daemon
apt-get install -y icecast2

echo "== 2. Verification de Liquidsoap =="
# Il existe un bug connu de segmentation fault avec le paquet Debian de
# Liquidsoap sur certaines versions ARM64/Bookworm (voir savonet/liquidsoap
# issue #3491). On verifie tout de suite plutot que de decouvrir le probleme
# plus tard.
if ! timeout 20 liquidsoap --version >/tmp/liquidsoap_version.log 2>&1; then
  echo "!! ATTENTION : 'liquidsoap --version' a plante (segfault ?)."
  echo "   Ceci est un bug connu du paquet Debian sur certains Raspberry Pi"
  echo "   (Bookworm/ARM64). Solution de repli recommandee : utiliser l'image"
  echo "   Docker officielle savonet/liquidsoap a la place du paquet apt."
  echo "   Voir la section 'Depannage Liquidsoap sur Raspberry Pi' du README."
  echo "   Journal : $(cat /tmp/liquidsoap_version.log)"
  read -r -p "Continuer quand meme l'installation ? [o/N] " reponse
  [[ "$reponse" =~ ^[oO]$ ]] || exit 1
else
  echo "Liquidsoap OK : $(cat /tmp/liquidsoap_version.log | head -1)"
fi

echo "== 3. Utilisateur dedie =="
id -u radio &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin radio

echo "== 4. Arborescence =="
mkdir -p "$RADIO_HOME"/{app,liquidsoap,media/musiques,media/jingles,media/pubs}
cp -r ../app/. "$RADIO_HOME/app/"
cp ../liquidsoap/radio.liq "$RADIO_HOME/liquidsoap/radio.liq"

echo "== 5. Generation automatique des mots de passe/cles =="
ICECAST_SOURCE_PW="$(openssl rand -hex 12)"
ICECAST_ADMIN_PW="$(openssl rand -hex 12)"
FLASK_SECRET="$(openssl rand -hex 24)"

# radio.liq : mot de passe source Icecast
sed -i "s/changeme_icecast_source_password/${ICECAST_SOURCE_PW}/" "$RADIO_HOME/liquidsoap/radio.liq"

# icecast2 : remplace les mots de passe par defaut ("hackme") du paquet
if [ -f /etc/icecast2/icecast.xml ]; then
  cp /etc/icecast2/icecast.xml /etc/icecast2/icecast.xml.bak
  sed -i "s/<source-password>.*<\/source-password>/<source-password>${ICECAST_SOURCE_PW}<\/source-password>/" /etc/icecast2/icecast.xml
  sed -i "s/<admin-password>.*<\/admin-password>/<admin-password>${ICECAST_ADMIN_PW}<\/admin-password>/" /etc/icecast2/icecast.xml
  # Active le service au demarrage (necessaire sur Debian/RPi OS)
  if [ -f /etc/default/icecast2 ]; then
    sed -i "s/ENABLE=false/ENABLE=true/" /etc/default/icecast2
  fi
fi

echo "Mot de passe source Icecast (deja injecte dans radio.liq) : ${ICECAST_SOURCE_PW}"
echo "Mot de passe admin Icecast (interface web Icecast /admin) : ${ICECAST_ADMIN_PW}"
echo "-> notez-les quelque part, ils ne seront plus jamais affiches en clair."

echo "== 6. Services systemd =="
cp liquidsoap-radio.service /etc/systemd/system/
sed "s/RADIO_SECRET_KEY=CHANGEZ-MOI/RADIO_SECRET_KEY=${FLASK_SECRET}/" radio-web.service > /etc/systemd/system/radio-web.service

# Redemarrage preventif quotidien de liquidsoap-radio (4h du matin par
# defaut) : objectif fonctionnement 24h/24 7j/7 sans intervention manuelle,
# meme si une derive memoire/CPU ne fait pas planter le processus (voir le
# commentaire dans liquidsoap-radio-restart.timer). Modifiable apres coup en
# editant /etc/systemd/system/liquidsoap-radio-restart.timer.
cp liquidsoap-radio-restart.service /etc/systemd/system/
cp liquidsoap-radio-restart.timer /etc/systemd/system/

systemctl daemon-reload

# Autorise l'utilisateur "radio" a redemarrer liquidsoap-radio sans mot de
# passe (bouton "Redemarrer Liquidsoap maintenant" dans Reglages -> Traitement
# audio, necessaire pour appliquer le fondu enchaine sans SSH).
cp radio-sudoers /etc/sudoers.d/radio-wra
chmod 0440 /etc/sudoers.d/radio-wra
visudo -c

chown -R radio:radio "$RADIO_HOME"

echo "== 7. Environnement Python =="
sudo -u radio python3 -m venv "$RADIO_HOME/app/venv"
sudo -u radio "$RADIO_HOME/app/venv/bin/pip" install --upgrade pip
sudo -u radio "$RADIO_HOME/app/venv/bin/pip" install -r "$RADIO_HOME/app/requirements.txt"

echo "== 8. Demarrage =="
systemctl restart icecast2
systemctl enable icecast2 >/dev/null
systemctl enable --now liquidsoap-radio.service
systemctl enable --now radio-web.service
systemctl enable --now liquidsoap-radio-restart.timer

echo "== 9. Nginx (optionnel) =="
echo "Voir nginx-radio.conf si vous preferez exposer l'interface sur le port"
echo "80 plutot que directement sur le port 5000."

HOSTNAME_LOCAL="$(hostname).local"

cat <<EOF

Installation terminee.

Interface d'administration : http://${HOSTNAME_LOCAL}:5000 (ou http://<ip-du-pi>:5000)
Identifiants par defaut     : admin / changeme
  -> changez ce mot de passe des la premiere connexion (menu "Mon compte").

Verification rapide :
  systemctl status liquidsoap-radio radio-web icecast2
  journalctl -u liquidsoap-radio -f     # logs Liquidsoap en direct
  journalctl -u radio-web -f            # logs de l'appli web en direct

Prochaines etapes :
  1. Deposez quelques musiques/jingles/pubs depuis l'interface web.
  2. Reglez la rotation dans "Reglages".
  3. Pour ecouter le flux : http://${HOSTNAME_LOCAL}:8000/radio.mp3

EOF
