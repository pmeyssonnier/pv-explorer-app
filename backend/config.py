"""Configuration centrale du backend PV Explorer.

Constantes de réglage (modèle, RAG, garde-fous) + origines CORS + logger
partagé. Extrait de app.py pour que chaque module (routers, services) importe
sa configuration sans dépendre de l'application FastAPI elle-même.
"""
import os
import logging

# Charge le fichier .env en local (sans effet en production où les variables
# sont fournies par la plateforme). Ne plante pas si python-dotenv est absent.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Pinecone / modèle ───────────────────────────────────────────────────────
INDEX_NAME = "pv-explorer"
NAMESPACE = "pv"
CLAUDE_MODEL = "claude-sonnet-4-6"   # bon rapport qualité/coût pour du public

# ── RAG ─────────────────────────────────────────────────────────────────────
TOP_K = 30                           # passages récupérés (contexte donné à Claude).
                                     # Élevé pour couvrir les questions transversales
                                     # (ex. « évolution depuis 2012 ») sur ~4 400 points.
                                     # NB : le RAG reste sémantique — pour une agrégation
                                     # exhaustive par thème/année, voir l'endpoint /trend.
MAX_SOURCES = 8                      # sources AFFICHÉES dans l'UI (lisibilité) —
                                     # Claude reçoit les TOP_K, l'utilisateur voit le top.
MAX_QUESTION_LEN = 500               # garde-fou coût : longueur max d'une question
# Seuil de pertinence minimal (score cosinus) pour afficher une source.
# 0.0 = désactivé. Les scores e5 sont resserrés : à calibrer sur des exemples
# réels avant de relever (ex. 0.80) pour masquer les sources hors-sujet.
SCORE_MIN = 0.0

# Chemin du fichier JSON des PV (lu par les statistiques). Surchargeable par env.
PV_JSON_PATH = os.environ.get("PV_JSON_PATH", "pv_conseil_schaerbeek.json")

logger = logging.getLogger("pv-explorer")

# ── CORS ────────────────────────────────────────────────────────────────────
# En production, définis ALLOWED_ORIGINS avec l'URL exacte du frontend, séparées
# par des virgules :
#   ALLOWED_ORIGINS=https://pv-explorer.vercel.app,https://pv-schaerbeek.vercel.app
# Par défaut (dev) : localhost uniquement. Jamais "*" en production.
_default_origins = "http://localhost:8000,http://localhost:5500,http://127.0.0.1:5500"
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",")
    if o.strip()
]
