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

# Version applicative — SOURCE UNIQUE, exposée par GET /health et lue par le
# frontend (le menu Options l'affiche). Bumper ici seulement.
VERSION = "1.6.0"

# ── Pinecone / modèle ───────────────────────────────────────────────────────
INDEX_NAME = "pv-explorer"
NAMESPACE = "pv"
CLAUDE_MODEL = "claude-sonnet-4-6"   # bon rapport qualité/coût pour du public
# Modèles Claude autorisés pour l'override « modèle » par requête (menu Options).
# Toute valeur hors de cet ensemble retombe sur CLAUDE_MODEL (garde-fou coût).
ALLOWED_MODELS = {"claude-sonnet-4-6", "claude-haiku-4-5-20251001"}

# ── RAG ─────────────────────────────────────────────────────────────────────
TOP_K = 30                           # passages récupérés (contexte donné à Claude).
                                     # Élevé pour couvrir les questions transversales
                                     # (ex. « évolution depuis 2012 ») sur ~4 400 points.
                                     # NB : le RAG reste sémantique — pour une agrégation
                                     # exhaustive par thème/année, voir l'endpoint /trend.
MAX_SOURCES = 15                     # sources AFFICHÉES dans l'UI (lisibilité) —
                                     # Claude reçoit les TOP_K, l'utilisateur voit le top.
MAX_QUESTION_LEN = 500               # garde-fou coût : longueur max d'une question
# Seuil de pertinence minimal (score cosinus) pour afficher une source.
# 0.0 = désactivé (aucun filtrage). Les scores e5 sont resserrés (bande haute
# étroite) : NE PAS deviner une valeur — la calibrer avec backend/eval_rag.py,
# qui mesure la distribution des scores (dont le plancher de bruit des questions
# hors-sujet). Réglable par env pour ajuster sans redéployer de code.
SCORE_MIN = float(os.environ.get("SCORE_MIN", "0.0"))

# Timeouts (secondes) des appels réseau externes. Bornent la durée max qu'un
# thread du threadpool FastAPI peut rester retenu sur un appel lent ou bloqué :
# au-delà, l'appel échoue proprement (500) au lieu d'immobiliser un thread.
PINECONE_TIMEOUT = 15    # la recherche vectorielle est rapide (<2 s en régime normal)
ANTHROPIC_TIMEOUT = 45   # marge pour une génération complète (max_tokens=2048)

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
