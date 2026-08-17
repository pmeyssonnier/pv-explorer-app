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
import re
import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import anthropic
from pinecone import Pinecone

# Configuration, modèles, prompt et utilitaires extraits en modules dédiés
# (voir config.py, models/, prompts/, utils/) — app.py ne garde que le câblage.
from config import (
    INDEX_NAME, NAMESPACE, CLAUDE_MODEL, TOP_K, MAX_SOURCES, SCORE_MIN,
    PV_JSON_PATH, ALLOWED_ORIGINS, logger,
)
from models.api import QuestionRequest, Source, AnswerResponse
from prompts.rag import SYSTEM_PROMPT
from utils.text import _strip_accents, _canon_theme
from utils.dates import _year_filter, _describe_year_filter

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
    except Exception:
        # Ne pas exposer le détail de l'exception au client (fuite d'infos).
        logger.exception("Échec de la vérification de l'index Pinecone")
        checks["index_ok"] = False
    return checks


@app.post("/ask", response_model=AnswerResponse)
@limiter.limit("10/minute;100/day")
def ask(request: Request, req: QuestionRequest):
    """Question ouverte : recherche RAG + réponse Claude citée."""
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question vide")

    # Filtres optionnels (métadonnées Pinecone) :
    #  - commune : absent/"toutes" → recherche croisée (comportement historique)
    #  - année   : détectée dans la question (« en 2018 », « depuis 2015 »…) pour
    #              que les sources correspondent bien à la période demandée.
    query = {"inputs": {"text": question}, "top_k": TOP_K}
    filters = {}
    commune = (req.commune or "").strip().lower()
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
        results = index.search(namespace=NAMESPACE, query=query)
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

    # Normaliser le format des hits Pinecone.
    # Selon la version du SDK, un hit est un dict OU un objet msgspec dont
    # `.get("score")` ne fonctionne pas (seul `h["score"]` expose l'alias).
    # On tente donc plusieurs accès pour récupérer un score fiable.
    def hit_score(h) -> float:
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

    norm = []
    for h in matches:
        fields = h.get("fields", h.get("metadata", {})) if hasattr(h, "get") else {}
        norm.append({"metadata": fields, "score": hit_score(h)})

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
            model=CLAUDE_MODEL,
            max_tokens=2048,
            temperature=0.2,   # factuel et cité : on limite la créativité
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )
        # Extraction robuste : on prend le premier bloc de type "text"
        # (ne suppose pas que content[0] en soit un).
        answer = next(
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
    not_found = "je ne trouve pas" in answer.lower()
    sources = []
    if not not_found:
        for h in norm:
            if h["score"] < SCORE_MIN:
                continue
            meta = h["metadata"]
            sources.append(Source(
                date=str(meta.get("date", "")),
                sp=int(float(meta.get("sp") or 0)),
                titre=str(meta.get("titre", "")),
                decision=str(meta.get("decision", "")),
                score=round(float(h["score"]), 3),
            ))
            if len(sources) >= MAX_SOURCES:   # UI lisible ; Claude a reçu tous les TOP_K
                break

    return AnswerResponse(answer=answer, sources=sources)


@app.get("/stats")
@limiter.limit("30/minute")
def stats(request: Request):
    """
    Statistiques globales calculées depuis l'index.
    Pour le prototype : on lit un fichier JSON local si présent (plus rapide/précis
    que d'agréger depuis Pinecone). En production on brancherait une vraie agrégation.
    """
    json_path = PV_JSON_PATH
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
        if p.get("montant_eur") and not _is_excluded_amount(p):
            montant_total += p["montant_eur"]
        if (p.get("vote") or {}).get("type") == "vote_nominal":
            votes_non_unanimes += 1

    # Répartition par année (PV + points), pour le graphe de l'onglet Stats.
    pv_year = Counter()
    pts_year = Counter()
    themes_year = {}          # année -> Counter de thèmes canoniques
    themes_all = Counter()    # toutes années confondues (canoniques)
    for s in db.get("seances", []):
        y = ((s.get("seance", {}) or {}).get("date") or "")[:4]
        if not y:
            continue
        pv_year[y] += 1
        pts_year[y] += len(s.get("points", []))
        yc = themes_year.setdefault(y, Counter())
        for p in s.get("points", []):
            for t in (p.get("thematiques") or []):
                c = _canon_theme(t)
                yc[c] += 1
                themes_all[c] += 1
    pv_par_annee = [
        {"annee": y, "pv": pv_year[y], "points": pts_year[y]}
        for y in sorted(pv_year)
    ]
    # Top 12 thèmes par année + « toutes » — alimente le filtre synchronisé
    # du graphe des thématiques (clic sur une année → sujets de cette année).
    themes_par_annee = {"toutes": themes_all.most_common(12)}
    for y, yc in themes_year.items():
        themes_par_annee[y] = yc.most_common(12)

    return {
        "nb_seances": len(db.get("seances", [])),
        "nb_points": len(all_points),
        "montant_total_eur": round(montant_total, 2),
        "votes_non_unanimes": votes_non_unanimes,
        "pv_par_annee": pv_par_annee,
        "themes_par_annee": themes_par_annee,
        "top_thematiques": themes.most_common(10),
        "top_rubriques": rubriques.most_common(10),
        "decisions": decisions.most_common(),
        "seances_dates": [s.get("seance", {}).get("date") for s in db.get("seances", [])],
    }


# ── ÉVOLUTION PAR THÈME (agrégation exhaustive, non sémantique) ──────────────
# Mots trop génériques : ignorés dans le thème pour ne pas tout matcher.
_TREND_STOPWORDS = {
    "budget", "montant", "depense", "cout", "evolution", "evolue", "evoluer",
    "depuis", "total", "annuel", "communal", "commune", "schaerbeek", "conseil",
    "point", "euro", "pour", "des", "les", "aux", "sur", "dans", "quel", "quelle",
    "combien", "quels", "quelles", "avec", "une", "und",
}


# Synonymes par thème civique : chaque valeur est un RADICAL cherché en début
# de mot (accent-strippé). Étend le rappel (« propreté » → nettoyage, déchet…)
# sans les collisions d'un simple substring. Clé = radical repère du thème.
_THEME_SYNONYMS = {
    "proprete":    ["proprete", "nettoy", "dechet", "immondice", "salubrit",
                    "balay", "encombrant", "graffiti", "caniveau", "corbeille"],
    "mobilite":    ["mobilite", "velo", "pieton", "cyclable", "stationnement",
                    "trottoir"],
    "ecole":       ["ecole", "scolaire", "enseignement", "creche"],
    "culture":     ["culture", "culturel", "bibliotheque", "musee", "theatre",
                    "patrimoine"],
    "sport":       ["sport", "piscine", "stade", "gymnase"],
    "logement":    ["logement", "habitat", "locatif"],
    "climat":      ["climat", "environnement", "energie", "arbre"],
    # « police » écarté : ambigu (police d'assurance / tribunal de police).
    "securite":    ["securite", "prevention", "camera", "gardien", "incivilite"],
    "cpas":        ["cpas", "precarite", "insertion"],
    "voirie":      ["voirie", "chaussee", "trottoir", "egout", "asphalt",
                    "pave", "refection"],
    # « energie » écarté : capte les deals Eandis/Sibelga (intercommunale, ~M€).
    "environnement": ["environnement", "climat", "arbre", "plantation",
                      "biodiversite", "vegetal", "verdur"],
    "finance":     ["finance", "fiscal", "taxe", "redevance", "emprunt",
                    "tresorerie"],
}

# Types de points NON dépensiers : motions, questions orales et demandes
# d'habitants citent des montants sans engager de dépense communale
# (ex. motion « survol aérien » = 500 M€). Écartés de /trend.
_TREND_SKIP_TYPES = {"motion", "question_orale", "demande_habitant"}

# Documents dont le montant N'EST PAS une dépense discrétionnaire du thème :
# budgets globaux (total de l'entité), transferts/dotations à d'autres entités,
# et litiges juridiques (montant en cause, pas une dépense). Exclus de /trend.
_TREND_EXCLUDE = re.compile(
    r"(modification budgetaire|douzieme[s]? provisoire|comptes? (annuels|communaux)"
    r"|compte.{0,25}exercice|comptes \d{4}|comptes de l"
    r"|budget.{0,25}(exercice|ordinaire|extraordinaire|\d{4}|general|initial|participatif)"
    r"|dotation.{0,25}(police|cpas|zone)"
    r"|garantie communale|avance.{0,15}tresorerie|provision.{0,15}(pour|risque)"
    # opérations d'intercommunales (valeur d'actifs / statuts, pas une dépense) :
    r"|eandis|sibelga|interfin|intercommunale|modification.{0,15}statuts"
    r"|(affaire|aff)\s*c/|recours|contentieux|affaires juridiques)"
)

# Décisions non dépensières : le conseil constate / prend acte sans engager de
# dépense (ex. comptes d'un organisme présentés « pour information »).
_TREND_NONSPEND_DECISION = re.compile(
    r"prend (pour information|acte|connaissance)|pour information|prend note|constate"
)


def _is_excluded_amount(p: dict) -> bool:
    """True si le montant du point n'est PAS une dépense discrétionnaire de la
    commune (budget global, transfert/dotation, litige, opération
    d'intercommunale, ou acte non dépensier : motion, question, prise d'acte).
    Partagé par /stats et /trend pour des chiffres cohérents et non trompeurs."""
    if p.get("type") in _TREND_SKIP_TYPES:
        return True
    titre = _strip_accents((p.get("titre") or "").lower()).replace(".", "")
    if _TREND_EXCLUDE.search(titre):
        return True
    decision = _strip_accents((p.get("decision") or "").lower()).replace(".", "")
    return bool(_TREND_NONSPEND_DECISION.search(decision))


def _trend_tokens(theme: str) -> list[str]:
    """Radicaux à chercher pour le thème : sans accents, singularisés, étendus
    par synonymes si le thème correspond à une famille connue. On NE tronque
    PAS (« proprete » évite la collision avec « propres / fonds propres ») ;
    le matching se fait en début de mot (\\b), attrapant les flexions."""
    raw = re.findall(r"[a-z]{3,}", _strip_accents(theme.lower()))
    terms = set()
    for t in raw:
        if t in _TREND_STOPWORDS:
            continue
        if len(t) > 4 and t.endswith("s"):   # pluriel → singulier grossier
            t = t[:-1]
        grp = None
        for key, syns in _THEME_SYNONYMS.items():
            if t == key or t.startswith(key) or key.startswith(t):
                grp = syns
                break
        if grp:
            terms.update(grp)
        else:
            terms.add(t)
    return sorted(terms)


@app.get("/trend")
@limiter.limit("30/minute")
def trend(request: Request, theme: str = Query(..., min_length=2, max_length=60)):
    """Pour un thème, additionne les montants par année sur TOUS les points
    (balayage exhaustif par mots-clés). Répond aux questions d'évolution que le
    RAG top-k ne couvre pas (ex. « budget propreté depuis 2012 »)."""
    json_path = PV_JSON_PATH
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Données non disponibles")
    with open(json_path, encoding="utf-8") as f:
        db = json.load(f)

    tokens = _trend_tokens(theme)
    if not tokens:
        raise HTTPException(
            status_code=400,
            detail="Thème trop générique — précise un mot-clé (ex. propreté, mobilité, écoles).",
        )
    # Matching en DÉBUT de mot : \bproprete attrape « propreté(s) » mais pas
    # « propres / fonds propres ».
    patterns = [re.compile(r"\b" + re.escape(t)) for t in tokens]

    from collections import defaultdict
    by_year = defaultdict(lambda: {"points": 0, "avec_montant": 0, "total_eur": 0.0})
    items = []
    exclus = 0  # documents budgétaires globaux / transferts / litiges écartés
    for s in db.get("seances", []):
        date = (s.get("seance", {}) or {}).get("date") or ""
        year = date[:4] or "?"
        for p in s.get("points", []):
            blob = " ".join(str(p.get(k, "")) for k in
                            ("titre", "resume", "rubrique", "sous_rubrique"))
            blob += " " + " ".join(p.get("thematiques") or [])
            # .replace(".", "") : « C.P.A.S. » → « cpas » (sinon lettres isolées)
            nblob = _strip_accents(blob.lower()).replace(".", "")
            if not any(pat.search(nblob) for pat in patterns):
                continue
            if _is_excluded_amount(p):
                exclus += 1
                continue
            cell = by_year[year]
            cell["points"] += 1
            m = p.get("montant_eur")
            if m and m > 0:
                cell["avec_montant"] += 1
                cell["total_eur"] += float(m)
                items.append({
                    "date": date,
                    "sp": int(float(p.get("sp") or 0)),
                    "montant_eur": round(float(m), 2),
                    "titre": (p.get("titre") or "")[:120],
                    "decision": p.get("decision") or "",
                })

    annees = [
        {"annee": y, "points": v["points"],
         "points_avec_montant": v["avec_montant"], "total_eur": round(v["total_eur"], 2)}
        for y, v in sorted(by_year.items())
    ]
    items.sort(key=lambda x: -x["montant_eur"])
    return {
        "theme": theme,
        "annees": annees,
        "points_total": sum(a["points"] for a in annees),
        "total_eur": round(sum(a["total_eur"] for a in annees), 2),
        "documents_exclus": exclus,
        "top_items": items[:8],
        "note": ("Agrégation exhaustive des points mentionnant le thème. Montants "
                 "ponctuels (marchés, subsides, achats) — non consolidés en budget "
                 "officiel. Budgets globaux, dotations et litiges juridiques sont "
                 "écartés pour éviter les totaux trompeurs."),
    }
