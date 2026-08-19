"""Détection d'intention temporelle dans les questions citoyennes → filtre
Pinecone sur la métadonnée `year`. Cœur sensible : une mauvaise borne renvoie
des sources d'une autre période (réponse « crédible mais fausse »).
"""
import re

from utils.text import _strip_accents

_YEAR = r"(20[0-3]\d)"                       # année plausible 2000-2039
_YEAR_RE = re.compile(r"\b" + _YEAR + r"\b")

# Détection d'intention temporelle par regex ANCRÉES à l'année : le mot-clé doit
# précéder immédiatement l'année (à un préfixe neutre près). Indispensable —
# une simple recherche de sous-chaîne « des » matchait « décisions DES écoles
# en 2018 » et transformait `= 2018` en `>= 2018` (réponse temporellement fausse).
# Les motifs sont appliqués sur la question accent-strippée en minuscules.
_YEAR_PREFIX = r"(?:l'annee\s+|l'an\s+|fin\s+|debut\s+|mi-?\s*)?"
_RE_ENTRE = re.compile(r"\bentre\s+" + _YEAR + r"\s+et\s+" + _YEAR)
_RE_GTE = re.compile(
    r"\b(?:depuis|a partir de|a partir d'|a compter de|apres|des)\s+"
    + _YEAR_PREFIX + _YEAR
)
_RE_LTE = re.compile(
    r"\b(?:avant|jusqu'?en|jusqu'?au|jusqu'?a|jusque)\s+"
    + _YEAR_PREFIX + _YEAR
)
# Intention « comptable » : un compte/bilan d'exercice Y est approuvé en séance
# Y+1, un budget Y voté en séance Y-1/Y. Pour ces questions, « … 2011 » ne doit
# pas se limiter aux séances de 2011 (souvent inexistantes), mais couvrir la
# séance qui approuve réellement le document (voir _year_filter, étape 4).
_RE_FINANCE = re.compile(r"\b(?:compte|budget|bilan)")


def _year_filter(question: str):
    """Détecte une/des année(s) dans la question → filtre Pinecone sur `year`
    (numérique). None si aucune année. Permet aux sources de correspondre à la
    période demandée (« décisions en 2018 » ne doit pas citer 2025).
      « en 2018 » / « 2018 »               → {"$eq": 2018}
      « depuis / à partir de / après 2015 » → {"$gte": 2015}
      « avant / jusqu'en / jusqu'à 2016 »   → {"$lte": 2016}
      « entre 2015 et 2018 » / « de … à … »  → {"$gte": 2015, "$lte": 2018}
    Les bornes ne sont reconnues que si le mot-clé précède directement l'année,
    pour ne pas confondre « des écoles » (article) avec « dès 2015 » (borne).
    """
    years = [int(y) for y in _YEAR_RE.findall(question) if 2000 <= int(y) <= 2035]
    if not years:
        return None
    q = _strip_accents(question.lower())

    # 1. Fourchette explicite « entre X et Y »
    m = _RE_ENTRE.search(q)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return {"$gte": min(a, b), "$lte": max(a, b)}

    # 2. Deux années distinctes mentionnées (« de 2015 à 2018 ») → fourchette
    if len(set(years)) >= 2:
        return {"$gte": min(years), "$lte": max(years)}

    # 3. Borne basse / haute — uniquement si le mot-clé est collé à l'année
    m = _RE_GTE.search(q)
    if m:
        return {"$gte": int(m.group(1))}
    m = _RE_LTE.search(q)
    if m:
        return {"$lte": int(m.group(1))}

    # 4. Année exacte — sauf intention « comptable » (compte/budget/bilan) : le
    #    compte d'un exercice Y est voté en séance Y+1, un budget Y en séance
    #    Y-1/Y. On élargit alors à [Y-1, Y+1] pour attraper la séance qui
    #    approuve réellement « le compte/budget 2011 » (sinon 0 résultat, la
    #    base ne contenant pas toujours de séance de l'année demandée).
    #    N'affecte QUE l'année exacte : une borne/fourchette explicite (« depuis
    #    2015 », « entre 2015 et 2018 ») exprime déjà l'intention et est respectée.
    y = years[0]
    if _RE_FINANCE.search(q):
        return {"$gte": y - 1, "$lte": y + 1}
    return {"$eq": y}


def _describe_year_filter(yf: dict) -> str:
    """Formule la période d'un filtre `year` en français, pour un message
    « aucun point » honnête et précis (« en 2019 », « depuis 2015 »…)."""
    lo, hi = yf.get("$gte"), yf.get("$lte")
    eq = yf.get("$eq")
    if eq is not None:
        return f"en {eq}"
    if lo is not None and hi is not None:
        return f"entre {lo} et {hi}" if lo != hi else f"en {lo}"
    if lo is not None:
        return f"depuis {lo}"
    if hi is not None:
        return f"avant {hi}"
    return "pour la période demandée"
