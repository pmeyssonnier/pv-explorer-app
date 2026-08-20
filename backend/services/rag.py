"""Service RAG : recherche vectorielle Pinecone + réponse Claude citée.

Orchestration de l'endpoint /ask, isolée du câblage HTTP. `answer()` renvoie un
AnswerResponse prêt à sérialiser et lève HTTPException sur les erreurs externes
(recherche ou génération) pour préserver les codes/messages exacts de l'API.
"""
import os
from typing import Optional

import anthropic
from fastapi import HTTPException

from config import (
    NAMESPACE, CLAUDE_MODEL, ALLOWED_MODELS, TOP_K, MAX_SOURCES, SCORE_MIN,
    PINECONE_TIMEOUT, ANTHROPIC_TIMEOUT, logger,
)
from models.api import Source, AnswerResponse
from prompts.rag import SYSTEM_PROMPT
from utils.dates import _year_filter, _describe_year_filter
from services.pinecone_service import get_pinecone_index
from services.statistics import load_db


def _pdf_url_map() -> dict:
    """date ISO → URL du PDF officiel du PV, lue depuis le JSON (mtime-caché).
    Permet d'ajouter le lien aux sources sans réindexer Pinecone. {} si indispo."""
    try:
        db = load_db()
    except Exception:
        return {}
    return {
        (s.get("seance", {}) or {}).get("date"): (s.get("seance", {}) or {}).get("source_url")
        for s in db.get("seances", [])
    }

_anthropic_client: Optional[anthropic.Anthropic] = None


def get_anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY manquante côté serveur")
        # timeout : borne la durée d'un appel bloqué (APITimeoutError sinon).
        _anthropic_client = anthropic.Anthropic(api_key=key, timeout=ANTHROPIC_TIMEOUT)
    return _anthropic_client


def build_context(matches: list) -> str:
    """Assemble les passages récupérés en contexte pour Claude."""
    parts = []
    for m in matches:
        meta = m.get("metadata", {})
        parts.append(meta.get("chunk_text", ""))
    return "\n\n---\n\n".join(parts)


def _hit_score(h) -> float:
    """Score d'un hit Pinecone, robuste aux variantes de SDK (dict OU objet
    msgspec dont `.get('score')` échoue — seul `h['score']` expose l'alias)."""
    try:
        return float(h["score"])
    except (KeyError, TypeError, ValueError):
        pass
    for attr in ("score", "_score"):
        v = getattr(h, attr, None)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    if isinstance(h, dict):
        return float(h.get("_score") or h.get("score") or 0.0)
    return 0.0


def _clamp(val, default, lo, hi, cast):
    """Borne un override optionnel (menu Options) : None → défaut ; sinon cast
    puis clip dans [lo, hi]. Toute valeur invalide retombe sur le défaut."""
    if val is None:
        return default
    try:
        return max(lo, min(hi, cast(val)))
    except (TypeError, ValueError):
        return default


def answer(question_raw: str, commune_raw: Optional[str], *,
           top_k: Optional[int] = None, max_sources: Optional[int] = None,
           score_min: Optional[float] = None, model: Optional[str] = None) -> AnswerResponse:
    """Question ouverte → recherche RAG filtrée (commune + année) + réponse Claude
    citée. Les overrides (menu Options) sont bornés ici. Lève HTTPException(400/500)
    sur entrée vide ou erreur externe."""
    question = (question_raw or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question vide")

    # Overrides bornés côté serveur (le front ne peut pas forcer des valeurs
    # coûteuses/dangereuses). Un modèle hors allowlist retombe sur le défaut.
    top_k = _clamp(top_k, TOP_K, 5, 50, int)
    max_sources = _clamp(max_sources, MAX_SOURCES, 1, 30, int)
    score_min = _clamp(score_min, SCORE_MIN, 0.0, 0.95, float)
    model = model if model in ALLOWED_MODELS else CLAUDE_MODEL

    # Filtres optionnels (métadonnées Pinecone) :
    #  - commune : absent/"toutes" → recherche croisée (comportement historique)
    #  - année   : détectée dans la question (« en 2018 », « depuis 2015 »…) pour
    #              que les sources correspondent bien à la période demandée.
    query = {"inputs": {"text": question}, "top_k": top_k}
    filters = {}
    commune = (commune_raw or "").strip().lower()
    if commune and commune not in ("toutes", "toute", "all", "tous"):
        filters["commune"] = {"$eq": commune}
    year_filter = _year_filter(question)
    if year_filter:
        filters["year"] = year_filter
    if filters:
        query["filter"] = filters

    # 1. Recherche vectorielle (embedding intégré Pinecone)
    try:
        index = get_pinecone_index()
        results = index.search(namespace=NAMESPACE, query=query, timeout=PINECONE_TIMEOUT)
        matches = results.get("result", {}).get("hits", [])
        # PAS de repli sans le filtre année : mieux vaut répondre honnêtement
        # « aucun point pour cette période » que de citer silencieusement une
        # autre année (l'index est réindexé avec le champ `year`).
    except Exception as e:
        # Cas particulier : quota mensuel d'embedding Pinecone épuisé (429
        # RESOURCE_EXHAUSTED). La recherche embed la question à la volée ; sans
        # quota, impossible. On répond clairement (200) plutôt qu'une 500
        # anxiogène — les onglets Statistiques/Évolution, eux, n'embeddent pas.
        blob = f"{type(e).__name__} {e}"
        if any(k in blob for k in ("RESOURCE_EXHAUSTED", "RateLimit", "429", "token limit")):
            logger.warning("Quota d'embedding Pinecone atteint sur /ask : %s", e)
            return AnswerResponse(
                answer=(
                    "La recherche dans les procès-verbaux est momentanément "
                    "indisponible : la limite mensuelle du service de recherche a "
                    "été atteinte. Les onglets « Statistiques » et « Évolution par "
                    "thème » restent pleinement accessibles en attendant."
                ),
                sources=[]
            )
        logger.exception("Erreur lors de la recherche vectorielle Pinecone")
        raise HTTPException(
            status_code=500,
            detail="Erreur interne lors de la recherche. Réessayez plus tard.",
        )

    if not matches:
        if year_filter:
            periode = _describe_year_filter(year_filter)
            return AnswerResponse(
                answer=(
                    f"Je n'ai trouvé aucun point du Conseil communal correspondant "
                    f"à votre recherche {periode}. Cette période n'est peut-être pas "
                    f"encore couverte par les procès-verbaux disponibles, ou aucun "
                    f"point ne correspond à votre question sur ces années."
                ),
                sources=[]
            )
        return AnswerResponse(
            answer="Je ne trouve aucun passage pertinent dans les procès-verbaux disponibles.",
            sources=[]
        )

    norm = []
    for h in matches:
        fields = h.get("fields", h.get("metadata", {})) if hasattr(h, "get") else {}
        norm.append({"metadata": fields, "score": _hit_score(h)})

    # 2. Construire le contexte et interroger Claude.
    # Les extraits et la question (non fiable) sont isolés dans des balises :
    # le system prompt interdit d'obéir à des instructions dans <question>.
    context = build_context(norm)
    user_prompt = f"""Voici des extraits de procès-verbaux du Conseil communal de Schaerbeek :

<extraits>
{context}
</extraits>

<question>
{question}
</question>

Réponds en te basant uniquement sur les <extraits>, et cite les séances et numéros de points."""

    try:
        client = get_anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=0.2,   # factuel et cité : on limite la créativité
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )
        # Extraction robuste : on prend le premier bloc de type "text"
        # (ne suppose pas que content[0] en soit un).
        answer_text = next(
            (b.text for b in response.content if getattr(b, "type", None) == "text"),
            "",
        )
    except Exception:
        logger.exception("Erreur lors de l'appel à Claude")
        raise HTTPException(
            status_code=500,
            detail="Erreur interne lors de la génération de la réponse. Réessayez plus tard.",
        )

    # 3. Construire la liste des sources.
    # Si le modèle indique ne pas avoir trouvé l'information, on n'affiche
    # aucune source (évite d'afficher « 8 délibérations » pour une réponse
    # « je ne trouve pas »). Sinon on filtre sous le seuil de pertinence.
    not_found = "je ne trouve pas" in answer_text.lower()
    sources = []
    if not not_found:
        url_map = _pdf_url_map()
        for h in norm:
            if h["score"] < score_min:
                continue
            meta = h["metadata"]
            date_str = str(meta.get("date", ""))
            source_type = str(meta.get("source_type") or "pv")
            # url : deep-link vidéo (porté par la métadonnée pour les débats
            # filmés), sinon lien PDF du PV résolu par date.
            url = meta.get("url") or url_map.get(date_str)
            sources.append(Source(
                date=date_str,
                sp=int(float(meta.get("sp") or 0)),
                titre=str(meta.get("titre", "")),
                decision=str(meta.get("decision", "")),
                score=round(float(h["score"]), 3),
                url=url,
                source_type=source_type,
            ))
            if len(sources) >= max_sources:   # UI lisible ; Claude a reçu tous les TOP_K
                break

    return AnswerResponse(answer=answer_text, sources=sources)
