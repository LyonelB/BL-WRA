"""
Petit client HTTP vers l'API locale exposee par Liquidsoap (voir la section
"API HTTP locale" dans liquidsoap/radio.liq, sur le port harbor_port,
127.0.0.1 uniquement).

Volontairement tres simple : Liquidsoap tourne sur la meme machine, on
n'a pas besoin d'un vrai client telnet, quelques requetes HTTP suffisent.
"""

import logging

import requests

log = logging.getLogger("liquidsoap_client")


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
