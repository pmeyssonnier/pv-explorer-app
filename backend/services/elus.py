"""Agrégation « Interventions par élu·e » : recherche STRUCTURÉE et exhaustive,
lue depuis la base JSON des PV (+ chapitrage vidéo), sans embedding ni Pinecone.

Motivation : la recherche sémantique du chat (/ask) est sensible à la
formulation (« Georges Verzin » vs « Verzin ») et non exhaustive. Ici on
agrège de façon déterministe l'ensemble des interventions d'une personne.

Deux rôles distincts — car un·e élu·e n'a pas la même activité selon son mandat :
  • AUTEUR·E (activité de conseiller·ère) : questions orales, demandes, motions
    qu'il/elle dépose. Attribution :
        - question orale  → 1er intervenant (fiable en séance)
        - demande         → auteur du titre (« Demande de M. X »), sinon 1er intervenant
        - motion          → auteur du titre UNIQUEMENT (les motions sont souvent
                            collectives : ne pas deviner à partir des intervenants)
        - débat filmé     → champ « auteur » du chapitrage vidéo
  • RÉPONDANT·E (activité de membre du Collège / échevin·e) : points où la
    personne répond en séance (champ « repondant »). Les libellés composés
    (« X et le Bourgmestre ») sont scindés ; un rôle seul (« Bourgmestre »)
    est résolu via les métadonnées de la séance.

Fonctions pures + cache mémoire par mtime (les fichiers ne changent qu'au
redéploiement). Les consommateurs ne font que LIRE le dict → partage sûr.
"""
import os
import re
import json
import unicodedata
from collections import defaultdict

from services.statistics import load_db
from utils.video import video_session_map

_VIDEO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "video_conseil_schaerbeek.json"
)

# ── Normalisation des noms ───────────────────────────────────────────────────
# Civilités en tête de nom (répétables : « M. le … »).
_CIV = re.compile(
    r"^(?:M\.?|Mme\.?|Mr\.?|Monsieur|Madame|Mlle|Dr\.?|de\s+heer|Mevrouw|Mevr\.?|Dhr\.?|Mijnheer)\s+",
    re.I,
)
# Auteur mentionné dans le titre d'une demande/motion (« Motion de M. Axel Bernard »).
_AUTHOR_IN_TITLE = re.compile(
    r"(?:Demande|Motion|Interpellation|Verzoek|Motie)\s+d[eu']?\s+"
    r"(?:M\.?|Mme|Monsieur|Madame|de heer|Mevrouw)?\s*"
    r"([A-ZÀ-Ÿ][\wÀ-ÿ'’-]+(?:\s+[A-ZÀ-Ÿ][\wÀ-ÿ'’-]+){0,2})"
)
# Mots de rôle/fonction : jamais un nom de personne à eux seuls.
_ROLE_WORDS = {
    "bourgmestre", "echevin", "echevine", "president", "presidente", "secretaire",
    "conseiller", "conseillere", "ff", "schepen", "burgemeester", "voorzitter",
    "college", "le", "la", "les", "du", "des", "au", "the", "puis", "ensuite", "alors",
}
# Particules de nom conservées en minuscules à l'affichage.
_PARTICLES = {"de", "du", "des", "van", "von", "den", "der", "ter", "ten",
              "la", "le", "el", "di", "da", "d'", "of"}
# Séparateurs d'un répondant composé (« X et Y », « X puis Y »…).
_RESP_SPLIT = re.compile(r"\s+et\s+|\s+en\s+|\s+puis\s+|\s+ensuite\s+|&|,|;|/|\+", re.I)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _clean(raw: str) -> str:
    """Retire la fonction après virgule (« Noël, Bourgmestre » → « Noël ») et
    les civilités de tête (répétées)."""
    s = (raw or "").split(",")[0].strip()
    prev = None
    while prev != s:
        prev = s
        s = _CIV.sub("", s).strip()
    return s


def _is_role_token(tok: str) -> bool:
    t = _strip_accents(tok).lower().strip(".'’-")
    return t in _ROLE_WORDS or bool(re.fullmatch(r"f\.?f\.?", tok, re.I))


def _key(name: str) -> str:
    """Clé d'identité = nom de famille (dernier mot), accent-strippé/minuscule.
    Rapproche « Georges VERZIN » (vidéo) et « Verzin » (PV) → « verzin »."""
    toks = [t for t in re.split(r"\s+", _clean(name)) if t]
    return _strip_accents(toks[-1]).lower().strip("-'’.") if toks else ""


def _titlecase(name: str) -> str:
    """Casse d'affichage homogène : « DEGREZ » → « Degrez », particules en
    minuscules (« Yvan de Beauffort »)."""
    out = []
    for i, tok in enumerate(name.split()):
        low = tok.lower()
        if i > 0 and low in _PARTICLES:
            out.append(low)
        elif "-" in tok:
            out.append("-".join(w[:1].upper() + w[1:].lower() for w in tok.split("-")))
        else:
            out.append(tok[:1].upper() + tok[1:].lower())
    return " ".join(out)


def _respondents(raw: str, seance: dict) -> list:
    """Noms de personnes extraits d'un champ « repondant » (composé possible).
    Un rôle seul est résolu via les métadonnées de séance (bourgmestre / ff)."""
    names = []
    for part in _RESP_SPLIT.split(raw or ""):
        c = _clean(part).strip()
        if not c:
            continue
        toks = [t for t in c.split() if not _is_role_token(t)]
        if toks:
            names.append(" ".join(toks))
            continue
        low = _strip_accents(c).lower()  # rôle seul → résolution via la séance
        if "ff" in low or "f.f" in low:
            if seance.get("bourgmestre_ff"):
                names.append(seance["bourgmestre_ff"])
        elif "bourgmestre" in low or "burgemeester" in low:
            if seance.get("bourgmestre"):
                names.append(seance["bourgmestre"])
    return names


# ── Types de points → libellés d'affichage ───────────────────────────────────
_TYPE_LABEL = {
    "question_orale": "Question orale",
    "demande_habitant": "Demande",
    "motion": "Motion",
    "video": "Débat filmé",
}


def _author_of(point: dict):
    """Nom de l'auteur·e d'un point PV selon son type (None si non attribuable)."""
    typ = point.get("type")
    title = point.get("titre") or ""
    interv = point.get("intervenants") or []
    if typ == "question_orale":
        return interv[0] if interv else None
    if typ == "demande_habitant":
        m = _AUTHOR_IN_TITLE.search(title)
        return m.group(1) if m else (interv[0] if interv else None)
    if typ == "motion":
        m = _AUTHOR_IN_TITLE.search(title)
        return m.group(1) if m else None
    return None


# ── Construction de l'index (cache par mtime des deux fichiers sources) ───────
_cache = {"sig": None, "index": None}


def _load_video():
    try:
        with open(_VIDEO_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    return data.get("seances", data) if isinstance(data, dict) else data


def _sig():
    """Signature (mtime des fichiers sources) pour invalider le cache si l'un
    d'eux est remplacé sur place (dev / redéploiement)."""
    parts = []
    try:
        from config import PV_JSON_PATH
        parts.append(os.path.getmtime(PV_JSON_PATH) if os.path.exists(PV_JSON_PATH) else 0)
    except Exception:
        parts.append(0)
    parts.append(os.path.getmtime(_VIDEO_PATH) if os.path.exists(_VIDEO_PATH) else 0)
    return tuple(parts)


def _role_of(n_depose: int, n_repond: int) -> str:
    """Étiquette de rôle dominante, dérivée de l'activité (indicatif)."""
    if n_repond >= 8 and n_repond > n_depose:
        return "college"      # échevin·e / bourgmestre (répond en séance)
    return "conseiller"       # conseiller·ère (dépose des points)


def _build_index() -> dict:
    pv = load_db().get("seances", [])
    people = defaultdict(lambda: {"variants": defaultdict(int), "depose": [], "repond": []})

    def add_variant(k, name):
        people[k]["variants"][_clean(name)] += 1

    pdf_by_date = {}
    session_map = video_session_map()

    for s in pv:
        meta = s.get("seance", {}) or {}
        date = meta.get("date")
        pdf_by_date[date] = meta.get("source_url")
        for p in s.get("points", []):
            author = _author_of(p)
            if author:
                last = author.split()[-1] if author.split() else ""
                if last and not _is_role_token(last):
                    k = _key(author)
                    if k:
                        add_variant(k, author)
                        people[k]["depose"].append({
                            "date": date,
                            "sp": p.get("sp") or 0,
                            "type": p.get("type"),
                            "titre": p.get("titre") or "",
                            "repondant": (p.get("repondant") or "").strip() or None,
                            "url": meta.get("source_url"),
                            "video_url": session_map.get(date),
                        })
            resp_raw = p.get("repondant")
            for name in _respondents(resp_raw, meta):
                k = _key(name)
                if not k:
                    continue
                add_variant(k, name)
                people[k]["repond"].append({
                    "date": date,
                    "sp": p.get("sp") or 0,
                    "titre": p.get("titre") or "",
                    "url": meta.get("source_url"),
                })

    for s in _load_video():
        date = s.get("date")
        for p in s.get("points", []):
            author = p.get("auteur")
            if not author:
                continue
            k = _key(author)
            if not k:
                continue
            add_variant(k, author)
            people[k]["depose"].append({
                "date": date,
                "sp": 0,
                "type": "video",
                "titre": p.get("titre_fr") or p.get("titre") or "",
                "repondant": None,
                "url": p.get("deeplink"),
                "video_url": s.get("video_url"),
            })

    index = {}
    for k, d in people.items():
        variants = d["variants"]
        # Nom d'affichage : la variante la plus complète, casse homogénéisée.
        best = max(variants, key=lambda v: (len(v.split()), len(v)))
        nom = _titlecase(best)
        d["depose"].sort(key=lambda it: (it["date"] or "", it["sp"]), reverse=True)
        d["repond"].sort(key=lambda it: (it["date"] or "", it["sp"]), reverse=True)
        index[k] = {
            "key": k,
            "nom": nom,
            "role": _role_of(len(d["depose"]), len(d["repond"])),
            "depose": d["depose"],
            "repond": d["repond"],
        }
    return index


def _index() -> dict:
    sig = _sig()
    if _cache["sig"] != sig:
        _cache["index"] = _build_index()
        _cache["sig"] = sig
    return _cache["index"]


# ── API publique (consommée par le routeur) ─────────────────────────────────
# Seuil d'affichage : écarte les artefacts d'extraction (une seule apparition).
_MIN_TOTAL = 2


def elus_list() -> list:
    """Liste des élu·e·s avec compteurs et rôle, triée par activité décroissante.
    Filtre les personnes à activité négligeable (< _MIN_TOTAL interventions)."""
    out = []
    for e in _index().values():
        nd, nr = len(e["depose"]), len(e["repond"])
        if nd + nr < _MIN_TOTAL:
            continue
        out.append({
            "key": e["key"],
            "nom": e["nom"],
            "role": e["role"],
            "depose": nd,
            "repond": nr,
        })
    out.sort(key=lambda x: (x["depose"] + x["repond"], x["depose"]), reverse=True)
    return out


def elu_detail(key: str):
    """Détail d'un·e élu·e (déposé + réponses) ou None si inconnu."""
    e = _index().get((key or "").strip().lower())
    if not e:
        return None

    def _fmt_depose(it):
        return {
            "date": it["date"],
            "sp": it["sp"],
            "type_label": _TYPE_LABEL.get(it["type"], "Point"),
            "type": it["type"],
            "titre": it["titre"],
            "repondant": it.get("repondant"),
            "url": it.get("url"),
            "video_url": it.get("video_url"),
        }

    def _fmt_repond(it):
        return {
            "date": it["date"],
            "sp": it["sp"],
            "titre": it["titre"],
            "url": it.get("url"),
        }

    depose = e["depose"]
    n_q = sum(1 for it in depose if it["type"] == "question_orale")
    n_d = sum(1 for it in depose if it["type"] == "demande_habitant")
    n_m = sum(1 for it in depose if it["type"] == "motion")
    n_v = sum(1 for it in depose if it["type"] == "video")
    return {
        "key": e["key"],
        "nom": e["nom"],
        "role": e["role"],
        "counts": {
            "depose": len(depose),
            "repond": len(e["repond"]),
            "questions": n_q,
            "demandes": n_d,
            "motions": n_m,
            "videos": n_v,
        },
        "depose": [_fmt_depose(it) for it in depose],
        "repond": [_fmt_repond(it) for it in e["repond"]],
    }
