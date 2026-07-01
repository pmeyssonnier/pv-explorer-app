"""
╔══════════════════════════════════════════════════════════════════════════╗
║  BACKEND API — PV EXPLORER (Q&R PV Schaerbeek)         v0.2 (durci)      ║
║  FastAPI : RAG (recherche + Claude) + statistiques                       ║
║  Les clés API restent CÔTÉ SERVEUR (jamais exposées au navigateur)       ║
║  Durcissements : CORS restreint (ALLOWED_ORIGINS) + rate limiting        ║
╚══════════════════════════════════════════════════════════════════════════╝

PRÉREQUIS :
    pip install fastapi uvicorn anthropic pinecone python-dotenv slowapi

VARIABLES D'ENVIRONNEMENT (fichier .env ou dashboard) :
    ANTHROPIC_API_KEY=sk-ant-...
    PINECONE_API_KEY=pcsk_...
    PV_JSON_PATH=pv_conseil_schaerbeek.json
    ALLOWED_ORIGINS=https://pv-explorer.vercel.app   (URL frontend, CORS)

SÉCURITÉ :
    - CORS : seules les origines de ALLOWED_ORIGINS sont acceptées (jamais "*").
    - Rate limiting (slowapi) : /ask = 10/min & 100/jour, /stats = 30/min, par IP.

LANCER EN LOCAL :
    uvicorn app:app --reload --port 8000

DÉPLOIEMENT SERVERLESS (Railway / Render) :
    - Commande de démarrage : uvicorn app:app --host 0.0.0.0 --port $PORT
    - Ajouter les variables d'environnement dans le dashboard
"""

import os
import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import anthropic
from pinecone import Pinecone

# Charge le fichier .env en local (sans effet en production où les variables
# sont fournies par la plateforme). Ne plante pas si python-dotenv est absent.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── CONFIG ──────────────────────────────────────────────────────────────────
INDEX_NAME = "pv-explorer"
NAMESPACE = "pv"
CLAUDE_MODEL = "claude-sonnet-4-6"   # bon rapport qualité/coût pour du public
TOP_K = 8                            # nb de passages récupérés par question

# Origines autorisées (CORS). En production, définis la variable d'environnement
# ALLOWED_ORIGINS avec l'URL exacte du frontend, séparées par des virgules :
#   ALLOWED_ORIGINS=https://pv-explorer.vercel.app,https://pv.schaerbeek.be
# Par défaut (dev) : localhost uniquement. Jamais "*" en production.
_default_origins = "http://localhost:8000,http://localhost:5500,http://127.0.0.1:5500"
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",")
    if o.strip()
]

# ── CLIENTS (init paresseuse) ────────────────────────────────────────────────
_anthropic_client: Optional[anthropic.Anthropic] = None
_pc: Optional[Pinecone] = None


def get_anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY manquante côté serveur")
        _anthropic_client = anthropic.Anthropic(api_key=key)
    return _anthropic_client


def get_pinecone_index():
    global _pc
    if _pc is None:
        key = os.environ.get("PINECONE_API_KEY", "")
        if not key:
            raise RuntimeError("PINECONE_API_KEY manquante côté serveur")
        _pc = Pinecone(api_key=key)
    return _pc.Index(INDEX_NAME)


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


# ── MODÈLES DE DONNÉES ────────────────────────────────────────────────────────
class QuestionRequest(BaseModel):
    question: str


class Source(BaseModel):
    date: str
    sp: int
    titre: str
    decision: str
    score: float


class AnswerResponse(BaseModel):
    answer: str
    sources: list[Source]


# ── SYSTEM PROMPT POUR LES RÉPONSES CITOYENNES ────────────────────────────────
SYSTEM_PROMPT = """Tu es l'assistant des procès-verbaux du Conseil communal de Schaerbeek.
Tu réponds aux questions des citoyens en te basant UNIQUEMENT sur les extraits de PV fournis.

RÈGLES :
- Réponds en français, de façon claire et accessible à tout citoyen.
- Base-toi EXCLUSIVEMENT sur les extraits fournis. N'invente jamais.
- Si l'information n'est pas dans les extraits, dis-le clairement : "Je ne trouve pas cette information dans les procès-verbaux disponibles."
- Cite toujours tes sources : mentionne la date de séance et le numéro de point (SP).
- Pour les montants, votes et décisions, sois précis.
- Reste neutre et factuel : tu rapportes ce qui a été décidé, sans prendre parti.
"""


def build_context(matches: list) -> str:
    """Assemble les passages récupérés en contexte pour Claude."""
    parts = []
    for m in matches:
        meta = m.get("metadata", {})
        parts.append(meta.get("chunk_text", ""))
    return "\n\n---\n\n".join(parts)


# ── ENDPOINTS ──────────────────────────────────────────────────────────────────
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
    except Exception as e:
        checks["index_ok"] = False
        checks["error"] = str(e)
    return checks


@app.post("/ask", response_model=AnswerResponse)
@limiter.limit("10/minute;100/day")
def ask(request: Request, req: QuestionRequest):
    """Question ouverte : recherche RAG + réponse Claude citée."""
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question vide")

    # 1. Recherche vectorielle (embedding intégré Pinecone)
    try:
        index = get_pinecone_index()
        results = index.search(
            namespace=NAMESPACE,
            query={"inputs": {"text": question}, "top_k": TOP_K}
        )
        matches = results.get("result", {}).get("hits", [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur recherche : {e}")

    if not matches:
        return AnswerResponse(
            answer="Je ne trouve aucun passage pertinent dans les procès-verbaux disponibles.",
            sources=[]
        )

    # Normaliser le format des hits Pinecone
    norm = []
    for h in matches:
        fields = h.get("fields", h.get("metadata", {}))
        norm.append({"metadata": fields, "score": h.get("_score", h.get("score", 0))})

    # 2. Construire le contexte et interroger Claude
    context = build_context(norm)
    user_prompt = f"""Voici des extraits de procès-verbaux du Conseil communal de Schaerbeek :

{context}

Question du citoyen : {question}

Réponds en te basant uniquement sur ces extraits, et cite les séances et numéros de points."""

    try:
        client = get_anthropic()
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )
        answer = response.content[0].text
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur Claude : {e}")

    # 3. Construire la liste des sources
    sources = []
    for h in norm:
        meta = h["metadata"]
        sources.append(Source(
            date=str(meta.get("date", "")),
            sp=int(meta.get("sp", 0)),
            titre=str(meta.get("titre", "")),
            decision=str(meta.get("decision", "")),
            score=round(float(h["score"]), 3),
        ))

    return AnswerResponse(answer=answer, sources=sources)


@app.get("/stats")
@limiter.limit("30/minute")
def stats(request: Request):
    """
    Statistiques globales calculées depuis l'index.
    Pour le prototype : on lit un fichier JSON local si présent (plus rapide/précis
    que d'agréger depuis Pinecone). En production on brancherait une vraie agrégation.
    """
    json_path = os.environ.get("PV_JSON_PATH", "pv_conseil_schaerbeek.json")
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Fichier de stats non disponible")

    with open(json_path, encoding="utf-8") as f:
        db = json.load(f)

    all_points = [p for s in db.get("seances", []) for p in s.get("points", [])]

    # Agrégations
    from collections import Counter
    themes = Counter()
    rubriques = Counter()
    decisions = Counter()
    montant_total = 0.0
    votes_non_unanimes = 0

    for p in all_points:
        for t in (p.get("thematiques") or []):
            themes[t] += 1
        rubriques[p.get("rubrique", "?")] += 1
        decisions[p.get("decision", "?")] += 1
        if p.get("montant_eur"):
            montant_total += p["montant_eur"]
        if (p.get("vote") or {}).get("type") == "vote_nominal":
            votes_non_unanimes += 1

    return {
        "nb_seances": len(db.get("seances", [])),
        "nb_points": len(all_points),
        "montant_total_eur": round(montant_total, 2),
        "votes_non_unanimes": votes_non_unanimes,
        "top_thematiques": themes.most_common(10),
        "top_rubriques": rubriques.most_common(10),
        "decisions": decisions.most_common(),
        "seances_dates": [s["seance"].get("date") for s in db.get("seances", [])],
    }
