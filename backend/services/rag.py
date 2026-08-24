"""Service RAG : recherche vectorielle Pinecone + réponse Claude citée.

Orchestration de l'endpoint /ask, isolée du câblage HTTP. `answer()` renvoie un
AnswerResponse prêt à sérialiser et lève HTTPException sur les erreurs externes
(recherche ou génération) pour préserver les codes/messages exacts de l'API.
"""
import os
import re
from typing import Optional

import anthropic
from fastapi import HTTPException

from config import (
    NAMESPACE, CLAUDE_MODEL, ALLOWED_MODELS, TOP_K, MAX_SOURCES, SCORE_MIN,
    PINECONE_TIMEOUT, ANTHROPIC_TIMEOUT, logger,
)
import lexique_store
from models.api import Source, AnswerResponse
from prompts.rag import SYSTEM_PROMPT
from utils.dates import _year_filter, _describe_year_filter
from utils.text import _decision_status, _strip_accents, _thematique_label
from utils.video import video_session_map, video_chunk_counts
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


def _glossaire_block(question: str, context: str) -> str:
    """Bloc <glossaire> des définitions du lexique (voir lexique_store) dont le
    TERME apparaît dans la question OU dans les extraits — aide Claude à
    comprendre et reformuler le vocabulaire local (ex. « dropzone »). Vide si
    aucun terme ne s'y trouve. Comparaison insensible à la casse et aux accents.
    Ces définitions servent la COMPRÉHENSION, ce ne sont pas des sources citables
    (le system prompt l'énonce)."""
    gloss = lexique_store.glossaire()
    if not gloss:
        return ""
    hay = _strip_accents(f"{question}\n{context}".lower())
    hits = [(t, d) for t, d in gloss.items() if _strip_accents(str(t).lower()) in hay]
    if not hits:
        return ""
    lignes = "\n".join(f"- {t} : {d}" for t, d in hits)
    return (
        "<glossaire>\n"
        "Définitions de vocabulaire local, pour t'aider à comprendre et expliquer "
        "les extraits. Ce ne sont PAS des extraits de PV : ne les cite pas comme source.\n"
        f"{lignes}\n"
        "</glossaire>\n\n"
    )


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


def _hit_id(h) -> str:
    """ID d'un hit Pinecone, robuste aux variantes de SDK (voir _hit_score)."""
    try:
        return str(h["_id"])
    except (KeyError, TypeError):
        pass
    for attr in ("id", "_id"):
        v = getattr(h, attr, None)
        if v is not None:
            return str(v)
    if isinstance(h, dict):
        return str(h.get("id") or h.get("_id") or "")
    return ""


# Un point de chapitrage vidéo peut produire PLUSIEURS chunks indexés (un
# chunk « titre » + un chunk par sous-segment de transcript aligné — voir
# index_pv.video_point_to_chunks), qui partagent le même id de base
# (video-<id>-<start>) avec un suffixe « -t<sous_start> » pour les extraits
# de transcript. _point_key() retire ce suffixe pour regrouper ces chunks
# sous UNE seule source affichée (sinon le même point apparaîtrait plusieurs
# fois dans la liste des sources). Sans effet sur les PV (un seul chunk par
# point, id sans ce suffixe).
_TRANSCRIPT_SUFFIX = re.compile(r"-t\d+$")


def _point_key(chunk_id: str) -> str:
    return _TRANSCRIPT_SUFFIX.sub("", chunk_id) if chunk_id else chunk_id


# Reconstruit le deep-link vers le DÉBUT du point (pas le sous-segment de
# transcript le mieux classé, potentiellement plusieurs minutes plus tard).
# Le titre affiché pour une source vidéo est toujours celui du POINT (jamais
# celui d'un extrait précis) — le lien doit rester cohérent avec ce titre.
# `.+` est glouton : il capture correctement un video_id contenant lui-même
# des tirets (ex. "-UIRlJYM26M"), le `-\d+$` final n'ancrant que le start_s.
_POINT_ID_RE = re.compile(r"^video-(.+)-(\d+)$")


def _point_video_url(point_key: str):
    m = _POINT_ID_RE.match(point_key or "")
    if not m:
        return None
    video_id, start = m.group(1), m.group(2)
    return f"https://www.youtube.com/watch?v={video_id}&t={start}s"


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
            logger.warning(
                "Quota d'embedding Pinecone atteint sur /ask : %s (commune=%r)", e, commune
            )
            return AnswerResponse(
                answer=(
                    "La recherche dans les procès-verbaux est momentanément "
                    "indisponible : la limite mensuelle du service de recherche a "
                    "été atteinte. Les onglets « Statistiques » et « Évolution par "
                    "thème » restent pleinement accessibles en attendant."
                ),
                sources=[]
            )
        # Contexte utile au diagnostic sans logger le texte de la question
        # (outil citoyen public — peut contenir des données personnelles).
        logger.exception(
            "Erreur lors de la recherche vectorielle Pinecone "
            "(commune=%r, top_k=%d, longueur_question=%d)",
            commune, top_k, len(question),
        )
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
        norm.append({"metadata": fields, "score": _hit_score(h), "id": _hit_id(h)})

    # 2. Construire le contexte et interroger Claude.
    # Les extraits et la question (non fiable) sont isolés dans des balises :
    # le system prompt interdit d'obéir à des instructions dans <question>.
    context = build_context(norm)
    glossaire = _glossaire_block(question, context)
    user_prompt = f"""Voici des extraits de procès-verbaux du Conseil communal de Schaerbeek :

{glossaire}<extraits>
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
        logger.exception(
            "Erreur lors de l'appel à Claude (model=%r, commune=%r, longueur_question=%d)",
            model, commune, len(question),
        )
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
    seen_points = set()      # dédoublonnage par point (voir _point_key)
    url_map = _pdf_url_map()
    session_map = video_session_map()
    chunk_counts = video_chunk_counts()
    for h in norm:
        if h["score"] < score_min:
            continue
        meta = h["metadata"]
        source_type = str(meta.get("source_type") or "pv")
        # Sur une réponse « je ne trouve pas », on masque les délibérations (PV)
        # et les questions écrites pour ne pas afficher de fausses sources —
        # MAIS on garde les DÉBATS VIDÉO : le lien « ▶ voir le débat » reste
        # utile même quand le contenu n'est pas transcrit (le point a bien été
        # abordé au Conseil).
        if not_found and source_type != "video_conseil":
            continue
        # Un même point peut avoir produit plusieurs chunks (titre + extraits
        # de transcript) : norm est trié par score décroissant, donc le premier
        # chunk rencontré pour un point est le mieux classé — n'afficher QUE
        # celui-là (sinon le même point apparaîtrait plusieurs fois côté UI).
        point_key = _point_key(h.get("id", ""))
        if point_key and point_key in seen_points:
            continue
        if point_key:
            seen_points.add(point_key)
        date_str = str(meta.get("date", ""))
        # url : deep-link vidéo (porté par la métadonnée pour les délibérations),
        # sinon lien PDF du PV résolu par date. Pour un DÉBAT VIDÉO, on ignore le
        # `url` du chunk (qui peut être celui d'un sous-segment de transcript, en
        # plein milieu du débat) et on reconstruit systématiquement le lien vers
        # le DÉBUT du point — cohérent avec le titre affiché (toujours celui du
        # point, jamais celui d'un extrait précis).
        n_extraits = None
        if source_type == "video_conseil":
            url = _point_video_url(point_key) or meta.get("url")
            n_extraits = chunk_counts.get(point_key)
        elif source_type == "question_ecrite":
            # Pas de repli sur url_map (résolu par date de PV) : une question
            # écrite n'est jamais liée à une séance/un PV, même si sa date
            # coïncide par hasard avec celle d'un PV — voir
            # services/questions_ecrites*.py.
            url = meta.get("url") or None
        else:
            url = meta.get("url") or url_map.get(date_str)
        # Lien « voir la séance » (vidéo, début) : uniquement pour une
        # délibération (PV) dont la séance a été filmée, même sans chapitrage.
        # None pour un débat vidéo (déjà un deep-link précis) et pour une
        # question écrite (jamais liée à une séance, cf. ci-dessus).
        video_url = session_map.get(date_str) if source_type == "pv" else None
        # Statut de la décision : pour un débat vidéo OU une question écrite,
        # le champ « decision » porte déjà un contexte compact précalculé à
        # l'indexation (« Type · Auteur·e » / « Question de X · répondu par Y »),
        # pas une issue de vote — inchangé. Pour une délibération, normalisé +
        # issue du vote quand connue (ex. « Décidé à l'unanimité »), comme
        # partout ailleurs (Séances/Par élu·e) — voir _decision_status (limité
        # au TYPE de vote indexé, sans le détail pour/contre/abstentions).
        decision = (
            str(meta.get("decision", "")) if source_type in ("video_conseil", "question_ecrite")
            else _decision_status(meta.get("decision"), meta.get("vote_type"))
        )
        sources.append(Source(
            date=date_str,
            sp=int(float(meta.get("sp") or 0)),
            titre=str(meta.get("titre", "")),
            decision=decision,
            score=round(float(h["score"]), 3),
            url=url,
            source_type=source_type,
            video_url=video_url,
            n_extraits=n_extraits,
            thematiques=[_thematique_label(t) for t in (meta.get("thematiques") or [])],
            reponse=meta.get("reponse") or None if source_type == "question_ecrite" else None,
        ))
        if len(sources) >= max_sources:   # UI lisible ; Claude a reçu tous les TOP_K
            break

    return AnswerResponse(answer=answer_text, sources=sources)
