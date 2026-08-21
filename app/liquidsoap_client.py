"""
Petit client HTTP vers l'API locale exposee par Liquidsoap (voir la section
"API HTTP locale" dans liquidsoap/radio.liq, sur le port harbor_port,
127.0.0.1 uniquement).

Volontairement tres simple : Liquidsoap tourne sur la meme machine, on
n'a pas besoin d'un vrai client telnet, quelques requetes HTTP suffisent.
"""

import json
import logging
import os

import requests

log = logging.getLogger("liquidsoap_client")

# Correspondance entre les cles de la table settings (BDD) et le nom du
# traitement audio tel que connu de radio.liq / de l'API harbor /audio-fx.
AUDIO_FX_SETTINGS = {
    "audio_normalize_enabled": "normalize",
    "audio_crossfade_enabled": "crossfade",
    "audio_blank_removal_enabled": "blank_removal",
}

# Parmi ces 3 traitements, seuls ceux-la sont bascultables a chaud via l'API
# harbor (voir le commentaire "Traitement audio optionnel" dans radio.liq) :
# crossfade necessite un redemarrage de liquidsoap-radio.
LIVE_TOGGLABLE_AUDIO_FX = {"normalize", "blank_removal"}


class LiquidsoapUnavailable(Exception):
    """Liquidsoap ne repond pas (pas demarre, ou config differente)."""


def _get(base_url, path, params=None, timeout=3):
    try:
        resp = requests.get(f"{base_url}{path}", params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        log.warning("Appel Liquidsoap %s a echoue: %s", path, exc)
        raise LiquidsoapUnavailable(str(exc)) from exc


def push_file(base_url, filepath, category=None):
    """Insere immediatement un fichier (jingle/pub) dans la file prioritaire.

    "category" (jingle/pub) est transmis a Liquidsoap qui l'annote sur la
    requete : c'est ce qui permet au webhook on_track de savoir avec
    certitude de quoi il s'agit, sans deviner depuis le chemin du fichier.
    """
    params = {"file": filepath}
    if category:
        params["type"] = category
    return _get(base_url, "/push", params=params)


def skip(base_url):
    """Passe au titre suivant."""
    return _get(base_url, "/skip")


def ping(base_url):
    """Verifie que le script Liquidsoap tourne et repond."""
    try:
        return _get(base_url, "/ping", timeout=1.5)
    except LiquidsoapUnavailable:
        return None


def set_audio_fx(base_url, name, enabled):
    """Active/desactive a chaud un traitement audio (normalize/blank_removal
    uniquement, voir LIVE_TOGGLABLE_AUDIO_FX). Prend effet au prochain
    changement de titre, sans redemarrer Liquidsoap.
    """
    params = {"name": name, "value": "true" if enabled else "false"}
    return _get(base_url, "/audio-fx", params=params)


def write_audio_fx_file(path, settings):
    """Ecrit l'etat des 3 traitements audio dans le fichier JSON relu par
    radio.liq au demarrage (voir AUDIO_FX_JSON_PATH dans config.py).

    Necessaire meme pour normalize/blank_removal (bascultables a chaud) afin
    que Liquidsoap retrouve le dernier etat choisi s'il redemarre
    independamment de l'appli web (mise a jour, reboot...). Indispensable
    pour crossfade, qui n'est relu qu'a ce moment-la.
    """
    payload = {
        fx_name: str(settings.get(setting_key, "0")) in ("1", "true", "True")
        for setting_key, fx_name in AUDIO_FX_SETTINGS.items()
    }
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp_path, path)


def sync_audio_fx(base_url, settings):
    """Repousse vers Liquidsoap, a chaud, les traitements audio qui le
    permettent (normalize/blank_removal). Best-effort : renvoie la liste des
    noms qui ont echoue (ex: Liquidsoap pas encore demarre) sans lever
    d'exception - ecrire le fichier JSON (voir write_audio_fx_file) suffit a
    ce qu'ils soient repris correctement au prochain demarrage.
    """
    failed = []
    for setting_key, fx_name in AUDIO_FX_SETTINGS.items():
        if fx_name not in LIVE_TOGGLABLE_AUDIO_FX:
            continue
        enabled = str(settings.get(setting_key, "0")) in ("1", "true", "True")
        try:
            set_audio_fx(base_url, fx_name, enabled)
        except LiquidsoapUnavailable:
            failed.append(fx_name)
    return failed
