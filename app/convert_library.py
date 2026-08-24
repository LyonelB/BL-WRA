#!/usr/bin/env python3
"""
Script de maintenance ponctuel : convertit en MP3 44100Hz/stereo (sans
pochette) tous les fichiers de la bibliotheque (musiques/jingles/pubs) qui
ne sont pas deja dans ce format, et met a jour la base SQLite en
consequence (colonnes filename/duration de la table tracks). Le moteur de
conversion est partage avec le bouton "Relancer la conversion" de Reglages
-> Format audio (voir library_convert.py) - les deux font exactement la
meme chose, un CLI ponctuel etant plus pratique pour une premiere passe sur
une grosse bibliotheque existante que d'attendre depuis le navigateur.

Pourquoi : depuis l'ajout de la conversion automatique a l'import (voir
uploads.save_upload), tout nouvel ajout est deja uniformise. Ce script
traite la bibliotheque deja en ligne avant ce changement (melange de MP3
44100/48000Hz et de WAV non compresse), soupconnee de causer les
avertissements "Latency is too high" de Liquidsoap a chaque transition vers
un fichier d'un format different du pipeline audio (fixe a 44100Hz, voir
radio.liq).

Usage (sur le Raspberry Pi, une fois l'appli deployee) :

    cd /opt/radio/app
    sudo -u radio venv/bin/python3 convert_library.py --dry-run   # apercu
    sudo -u radio venv/bin/python3 convert_library.py             # execution

Par defaut, le bitrate cible est celui configure dans Reglages -> Format
audio (192kbps si jamais configure) ; --bitrate permet de forcer une autre
valeur ponctuellement.

IMPORTANT : a executer avec l'utilisateur "radio" (sudo -u radio), pas en
root - sinon les fichiers convertis appartiendraient a root et l'appli web
(qui tourne en tant que "radio") ne pourrait plus les supprimer/deplacer
ensuite. Le script est idempotent (relancer ne reconvertit pas ce qui est
deja au bon format) : en cas d'interruption (Ctrl+C, coupure), on peut le
relancer sans risque.

Sans danger pour le flux en cours : chaque fichier est reencode dans un
fichier temporaire puis bascule seulement une fois pret ; si un fichier en
cours de lecture par Liquidsoap est remplace/supprime pendant l'operation,
la lecture en cours n'est pas interrompue (sous Linux, un fichier ouvert
reste lisible par le processus qui l'a ouvert meme apres suppression/
remplacement sur le disque) - la prochaine lecture de ce titre utilisera la
version convertie.
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import library_convert


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Analyse seulement, ne convertit/modifie rien.")
    parser.add_argument(
        "--bitrate", type=int, default=None,
        help="Force un bitrate cible en kbps (par defaut : celui configure dans "
             "Reglages -> Format audio, 192 si jamais configure).",
    )
    args = parser.parse_args()

    if not os.path.exists(config.DB_PATH):
        print(f"Base introuvable : {config.DB_PATH}", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row

    bitrate = args.bitrate
    if bitrate is None:
        row = db.execute("SELECT value FROM settings WHERE key = 'audio_convert_bitrate'").fetchone()
        bitrate = int(row["value"]) if row else 192

    counts, total = library_convert.run(db, dry_run=args.dry_run, bitrate_kbps=bitrate, on_line=print)
    db.close()

    print()
    print(f"Bitrate cible : {bitrate}kbps")
    print("Resume :", ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "aucun fichier")
    if args.dry_run and counts.get("would_convert"):
        print("Aucun fichier modifie (--dry-run). Relancer sans --dry-run pour convertir.")


if __name__ == "__main__":
    main()
