"""
╔══════════════════════════════════════════════════════════════════════════╗
║  BACKEND API — PV EXPLORER (Q&R PV Schaerbeek)         v0.2 (durci)      ║
║  FastAPI : RAG (recherche + Claude) + statistiques                       ║
║  Les clés API restent CÔTÉ SERVEUR (jamais exposées au navigateur)       ║
║  Durcissements : CORS restreint (ALLOWED_ORIGINS) + rate limiting        ║
╚══════════════════════════════════════════════════════════════════════════╝

app.py est le POINT DE MONTAGE : il crée l'application FastAPI, le rate limiter
et le CORS, puis expose des endpoints FINS qui délèguent à la logique métier :
    config.py            constantes, CORS, logger
    models/api.py        schémas Pydantic (requêtes/réponses)
    prompts/rag.py       system prompt
    utils/{text,dates}   normalisation + détection temporelle
    services/rag.py           recherche RAG + réponse Claude (/ask)
    services/statistics.py    agrégations /stats et /trend (lecture JSON)
    services/pinecone_service.py   accès à l'index Pinecone

VARIABLES D'ENVIRONNEMENT (fichier .env ou dashboard) :
    ANTHROPIC_API_KEY, PINECONE_API_KEY, PV_JSON_PATH, ALLOWED_ORIGINS

LANCER EN LOCAL :   uvicorn app:app --reload --port 8000
DÉPLOIEMENT :       uvicorn app:app --host 0.0.0.0 --port $PORT
"""
import os

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config import ALLOWED_ORIGINS, logger
from models.api import QuestionRequest, AnswerResponse
from services.pinecone_service import get_pinecone_index
from services import rag, statistics

# ── APP ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="PV Schaerbeek Q&R", version="0.1")

# Rate limiting (slowapi) : protège la clé API contre l'abus.
# La limite par défaut s'applique à toutes les routes ; chaque endpoint peut
# la surcharger via le décorateur @limiter.limit(...).
limiter = Limiter(key_func=get_remote_address, default_limits=["200/day"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS : n'autorise que les origines déclarées (jamais "*" en production).
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── ENDPOINTS ────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"service": "PV Schaerbeek Q&R", "status": "ok", "version": "0.1"}


@app.get("/health")
def health():
    """Vérifie que les clés sont configurées et l'index accessible."""
    checks = {
        "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "pinecone_key": bool(os.environ.get("PINECONE_API_KEY")),
    }
    try:
        idx = get_pinecone_index()
        stats = idx.describe_index_stats()
        checks["index_vectors"] = stats.get("total_vector_count", 0)
        checks["index_ok"] = True
    except Exception:
        # Ne pas exposer le détail de l'exception au client (fuite d'infos).
        logger.exception("Échec de la vérification de l'index Pinecone")
        checks["index_ok"] = False
    return checks


@app.post("/ask", response_model=AnswerResponse)
@limiter.limit("10/minute;100/day")
def ask(request: Request, req: QuestionRequest):
    """Question ouverte : recherche RAG + réponse Claude citée (voir services.rag)."""
    return rag.answer(req.question, req.commune)


@app.get("/stats")
@limiter.limit("30/minute")
def stats(request: Request):
    """Statistiques globales calculées depuis la base JSON des PV."""
    try:
        db = statistics.load_db()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Fichier de stats non disponible")
    return statistics.compute_stats(db)


@app.get("/trend")
@limiter.limit("30/minute")
def trend(request: Request, theme: str = Query(..., min_length=2, max_length=60)):
    """Évolution d'un thème : montants agrégés par année (voir services.statistics)."""
    try:
        db = statistics.load_db()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Données non disponibles")
    try:
        return statistics.compute_trend(db, theme)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
