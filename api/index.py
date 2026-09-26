"""
Point d'entrée pour Vercel : Vercel exécute ce fichier comme une fonction serverless
et y cherche la variable `app` (application FastAPI).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from main import app, initialiser_base  # noqa: E402

# Vercel ne lance pas toujours les événements de démarrage : on prépare la base ici
# (création des tables et catégories si besoin, sans effet si c'est déjà fait).
initialiser_base()

__all__ = ["app"]
