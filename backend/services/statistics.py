"""Agrégations statistiques lues depuis la base JSON des PV (aucun embedding,
donc non soumis au quota Pinecone). Alimente les endpoints /stats et /trend.

Séparation nette : ces fonctions sont pures (db en entrée, dict en sortie) et
lèvent des exceptions Python simples (FileNotFoundError, ValueError) que la
couche HTTP traduit en codes de réponse.
"""
import os
import re
import json
from collections import Counter, defaultdict

from config import PV_JSON_PATH
from utils.text import _strip_accents, _canon_theme


# Cache mémoire du JSON parsé : /stats et /trend le relisaient (~7,6 Mo) à
# CHAQUE appel. Le fichier ne change qu'au redéploiement (conteneur immuable) ;
# on garde donc le dict parsé et on ne recharge que si le mtime change (utile
# en dev / si le fichier est remplacé sur place). Les consommateurs ne font que
# LIRE le dict, jamais le muter → partage sûr.
_db_cache = {"mtime": None, "db": None}


def load_db() -> dict:
    """Charge la base JSON des PV (mise en cache par mtime). Lève
    FileNotFoundError si le fichier manque."""
    if not os.path.exists(PV_JSON_PATH):
        raise FileNotFoundError(PV_JSON_PATH)
    mtime = os.path.getmtime(PV_JSON_PATH)
    if _db_cache["mtime"] != mtime:
        with open(PV_JSON_PATH, encoding="utf-8") as f:
            _db_cache["db"] = json.load(f)
        _db_cache["mtime"] = mtime
    return _db_cache["db"]


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


def compute_stats(db: dict) -> dict:
    """Statistiques globales : totaux, montants engagés (filtrés), répartition
    par année (PV + points) et thématiques (globales + par année canonisées)."""
    all_points = [p for s in db.get("seances", []) for p in s.get("points", [])]

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
    dates_year = defaultdict(list)   # année -> [dates ISO des séances]
    for s in db.get("seances", []):
        date = (s.get("seance", {}) or {}).get("date") or ""
        y = date[:4]
        if not y:
            continue
        pv_year[y] += 1
        pts_year[y] += len(s.get("points", []))
        dates_year[y].append(date)
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
    # Dates des séances par année — alimente le panneau « Séances par année »
    # (même interaction synchronisée que les thématiques : clic sur une année).
    dates_par_annee = {y: sorted(dates_year[y]) for y in dates_year}

    # Résumé COMPACT par séance : alimente le drill-down Année → Mois → Séance
    # côté frontend (KPI + thématiques recalculés à la volée pour chaque niveau,
    # sans nouvel appel serveur). Montant filtré comme le KPI global.
    seances_resume = []
    for s in db.get("seances", []):
        meta = s.get("seance", {}) or {}
        date = meta.get("date")
        if not date:
            continue
        pts = s.get("points", [])
        v = sum(1 for p in pts if (p.get("vote") or {}).get("type") == "vote_nominal")
        m = sum(p["montant_eur"] for p in pts
                if p.get("montant_eur") and not _is_excluded_amount(p))
        tc = Counter()                 # thème -> nb de points
        tm = defaultdict(float)        # thème -> montant engagé (même filtre que le KPI)
        for p in pts:
            mp = p["montant_eur"] if (p.get("montant_eur") and not _is_excluded_amount(p)) else 0
            for t in (p.get("thematiques") or []):
                c = _canon_theme(t)
                tc[c] += 1
                if mp:
                    tm[c] += mp
        # [thème, nb_points, montant] — trié par nb de points décroissant
        th_list = [[t, n, round(tm.get(t, 0.0), 2)] for t, n in tc.most_common()]
        seances_resume.append({
            "date": date,
            "points": len(pts),
            "votes": v,
            "montant": round(m, 2),
            "themes": th_list,
            "file": meta.get("source_file"),
            # URL publique du PDF : pass-through (à renseigner plus tard dans le JSON
            # source via un champ url/source_url) → le lien apparaît dès qu'elle existe.
            "url": meta.get("url") or meta.get("source_url"),
        })
    seances_resume.sort(key=lambda x: x["date"])

    return {
        "nb_seances": len(db.get("seances", [])),
        "nb_points": len(all_points),
        "montant_total_eur": round(montant_total, 2),
        "votes_non_unanimes": votes_non_unanimes,
        "pv_par_annee": pv_par_annee,
        "themes_par_annee": themes_par_annee,
        "dates_par_annee": dates_par_annee,
        "seances_resume": seances_resume,
        "top_thematiques": themes.most_common(10),
        "top_rubriques": rubriques.most_common(10),
        "decisions": decisions.most_common(),
        "seances_dates": [s.get("seance", {}).get("date") for s in db.get("seances", [])],
    }


def compute_trend(db: dict, theme: str) -> dict:
    """Pour un thème, additionne les montants par année sur TOUS les points
    (balayage exhaustif par mots-clés). Lève ValueError si le thème est trop
    générique (aucun radical exploitable)."""
    tokens = _trend_tokens(theme)
    if not tokens:
        raise ValueError(
            "Thème trop générique — précise un mot-clé (ex. propreté, mobilité, écoles)."
        )
    # Matching en DÉBUT de mot : \bproprete attrape « propreté(s) » mais pas
    # « propres / fonds propres ».
    patterns = [re.compile(r"\b" + re.escape(t)) for t in tokens]

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
