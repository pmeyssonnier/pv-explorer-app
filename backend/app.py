"""
╔══════════════════════════════════════════════════════════════════════════╗
║  BACKEND API — PV EXPLORER (Q&R PV Schaerbeek)         v0.2 (durci)      ║
║  FastAPI : RAG (recherche + Claude) + statistiques                       ║
║  Les clés API restent CÔTÉ SERVEUR (jamais exposées au navigateur)       ║
║  Durcissements : CORS restreint (ALLOWED_ORIGINS) + rate limiting        ║
╚══════════════════════════════════════════════════════════════════════════╝

app.py est le POINT DE MONTAGE : il crée l'application FastAPI, attache le rate
limiter et le CORS, puis monte les routers. Toute la logique vit ailleurs :
    config.py            constantes, CORS, logger
    limiter.py           rate limiter slowapi partagé
    models/api.py        schémas Pydantic (requêtes/réponses)
    prompts/rag.py       system prompt
    utils/{text,dates}   normalisation + détection temporelle
    services/rag.py            recherche RAG + réponse Claude (/ask)
    services/statistics.py     agrégations /stats et /trend (lecture JSON)
    services/pinecone_service.py   accès à l'index Pinecone
    routers/{health,ask,stats}     endpoints HTTP fins

VARIABLES D'ENVIRONNEMENT (fichier .env ou dashboard) :
    ANTHROPIC_API_KEY, PINECONE_API_KEY, PV_JSON_PATH, ALLOWED_ORIGINS

LANCER EN LOCAL :   uvicorn app:app --reload --port 8000
DÉPLOIEMENT :       uvicorn app:app --host 0.0.0.0 --port $PORT
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import ALLOWED_ORIGINS
from limiter import limiter
from routers import health, ask, stats

# ── APP ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="PV Schaerbeek Q&R", version="0.1")

# Rate limiting (slowapi) : le limiter partagé est attaché à l'app et son
# gestionnaire d'exception enregistré ; chaque route porte ses propres limites.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS : n'autorise que les origines déclarées (jamais "*" en production).
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── ROUTES ───────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(ask.router)
app.include_router(stats.router)
