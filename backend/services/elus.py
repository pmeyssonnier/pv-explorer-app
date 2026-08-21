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
    r"^(?:MM\.?|Messieurs|M\.?|Mme\.?|Mr\.?|Monsieur|Madame|Mesdames|Mlle|Dr\.?|"
    r"de\s+heer|Mevrouw|Mevr\.?|Dhr\.?|Mijnheer)\s+",
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
    "communal", "communale", "conseiller", "conseillere", "ff", "schepen",
    "burgemeester", "voorzitter", "college", "le", "la", "les", "du", "des", "au",
    "the", "puis", "ensuite", "alors",
}
# Particules de nom conservées en minuscules à l'affichage.
_PARTICLES = {"de", "du", "des", "van", "von", "den", "der", "ter", "ten",
              "la", "le", "el", "di", "da", "d'", "of"}
# Séparateurs d'un répondant composé (« X et Y », « X puis Y »…).
_RESP_SPLIT = re.compile(r"\s+et\s+|\s+en\s+|\s+puis\s+|\s+ensuite\s+|&|,|;|/|\+", re.I)

# Prénoms connus mais absents de toutes les sources PV/vidéo (la personne
# n'y est jamais mentionnée que par son seul nom de famille) : complétés
# manuellement, clé = _key() du nom de famille.
_DISPLAY_NAME_OVERRIDES = {
    "malingreau": "Alain Malingreau",
    "smeysters": "Christine Smeysters",
    "sobieski": "Christine Sobieski",
}


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
    return s.rstrip(".")


def _is_role_token(tok: str) -> bool:
    # Parenthèses incluses : « (ff) », « (Bourgmestre » (rôle entre
    # parenthèses, ex. « Jodogne (Bourgmestre ff) ») doivent être reconnus
    # comme des mots de rôle au même titre que sans parenthèses.
    t = _strip_accents(tok).lower().strip(".'’-()")
    return t in _ROLE_WORDS or bool(re.fullmatch(r"f\.?f\.?", t, re.I))


def _norm_tok(tok: str) -> str:
    return _strip_accents(tok).lower().strip("-'’.")


# Coquilles ponctuelles observées dans le chapitrage (ex. « Ouazrhrari » pour
# « Ouazrhari ») : la clé fautive est normalisée vers la clé correcte, pour
# éviter une fiche dupliquée. Un simple réordonnancement de mots (couvert par
# _build_name_registry/_PARTICLES ci-dessus) ne peut pas détecter une faute
# de frappe DANS un mot — d'où cette liste, à compléter au cas par cas.
_KEY_ALIASES = {"ouazrhrari": "ouazrhari"}

# Organismes captés à tort comme « auteur » sur des points de convention/
# partenariat du chapitrage vidéo (le contre-signataire, pas un·e citoyen·ne
# intervenant·e) : jamais une personne, à exclure de l'agrégation.
_NON_PERSON_VIDEO_AUTHORS = {"clad", "greentech vzw", "gemeente schaarbeek"}


def _is_non_person_video_author(raw: str) -> bool:
    return _strip_accents(_clean(raw)).lower() in _NON_PERSON_VIDEO_AUTHORS


def _key(name: str, pairs: set | None = None) -> str:
    """Clé d'identité = nom de famille, accent-strippé/minuscule.
    Rapproche « Georges VERZIN » (vidéo) et « Verzin » (PV) → « verzin ».

    Par défaut (sans registre) on suppose l'ordre « Prénom Nom » et on
    prend le dernier mot. Mais le PV mélange les deux ordres selon les
    séances (« Georges Verzin » / « Verzin Georges »), ce qui scindait une
    même personne en deux fiches (ex. Verzin à 64 interventions + un
    doublon « Georges » à 2). Un registre de paires (prénom, nom) déjà
    observées sans ambiguïté ailleurs dans le corpus (`_build_name_registry`)
    permet de reconnaître l'ordre inversé et de retrouver le vrai nom de
    famille, quel que soit le mot qui le porte dans cette mention-ci. On ne
    s'appuie jamais sur « ce mot est un nom de famille connu » tout seul :
    un même mot peut être le nom de famille d'une personne et le prénom
    d'une autre (ex. « Bernard » : nom de famille d'Axel Bernard, prénom du
    bourgmestre Bernard Clerfayt) — seule la paire complète est fiable.
    """
    toks = [t for t in re.split(r"\s+", _clean(name)) if t]
    if not toks:
        return ""
    normed = [_norm_tok(t) for t in toks]
    k = normed[-1]
    # Nom à particule en tête suivi d'autres mots (prénom) : ordre inversé
    # « (particule) Nom Prénom » (ex. « Van den Hove Quentin »,
    # « De Brabant Martin »). Le nom de famille se termine au premier mot
    # suivant la particule ; s'il ne reste rien après (« de Brabant » seul),
    # ce n'est pas un ordre inversé et le dernier mot ci-dessus convient déjà.
    if normed[0] in _PARTICLES and len(normed) > 1:
        j = 0
        while j < len(normed) and normed[j] in _PARTICLES:
            j += 1
        if j < len(normed) and j + 1 < len(normed):
            k = normed[j]
    elif len(normed) == 2 and pairs:
        a, b = normed
        if (b, a) in pairs:  # ordre inversé (« Nom Prénom ») détecté
            k = a
    return _KEY_ALIASES.get(k, k)


def _build_name_registry(pv: list, video: list) -> set:
    """Déduit du corpus les paires (prénom, nom) non ambiguës, pour lever
    l'ambiguïté d'ordre dans `_key`. Signal fiable : une mention à deux
    mots où un seul est tout en majuscules (« Georges VERZIN » → prénom
    Georges, nom de famille VERZIN, comme dans les PV officiels)."""
    pairs: set = set()

    def add_from(raw):
        toks = [t for t in _clean(raw).split() if t]
        if len(toks) == 2:
            caps = [t.isupper() and len(t) > 1 for t in toks]
            if sum(caps) == 1:
                idx = caps.index(True)
                sn, fn = _norm_tok(toks[idx]), _norm_tok(toks[1 - idx])
                pairs.add((fn, sn))

    for s in pv:
        meta = s.get("seance", {}) or {}
        for p in s.get("points", []):
            for interv in (p.get("intervenants") or []):
                add_from(interv)
            for name in _respondents(p.get("repondant") or "", meta):
                add_from(name)
            author = _author_of(p)
            if author:
                add_from(author)
    for s in video:
        for p in s.get("points", []):
            author = p.get("auteur")
            if author and not _is_non_person_video_author(author):
                add_from(author)
    return pairs


def _best_display_variant(variants: dict, key: str) -> str:
    """Choisit la meilleure variante brute pour l'affichage : on préfère
    celles qui se terminent bien par le nom de famille (la clé), donc déjà
    dans le bon ordre « Prénom Nom » (une variante en ordre inversé, ex.
    « Verzin Georges », se termine par le prénom et est donc écartée ici) et
    sans mot répété (artefact d'extraction, ex. « Bernard BERNARD »). Parmi
    les variantes admissibles, la plus complète (mots, puis longueur)."""
    def admissible(v):
        toks = [_norm_tok(t) for t in v.split()]
        return bool(toks) and toks[-1] == key and len(set(toks)) == len(toks)

    pool = [v for v in variants if admissible(v)] or list(variants)
    return max(pool, key=lambda v: (len(v.split()), len(v)))


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


def _looks_like_name(s: str) -> bool:
    """Garde-fou contre les artefacts d'extraction PDF : les métadonnées de
    séance (bourgmestre/bourgmestre_ff) sont censées contenir un nom de
    personne (≤ 4 mots), pas une phrase entière mal extraite du PV."""
    return bool(s) and 1 <= len(s.split()) <= 4


def _split_person_names(raw: str) -> list:
    """Découpe une mention potentiellement composée (« X et Y », « Bourgmestre
    X », « MM. X et Y ») en noms de personnes individuels, en écartant les
    mots de rôle purs (une part réduite à zéro mot après filtrage, ex. « le
    Bourgmestre » seul, est silencieusement ignorée : pas de nom à en tirer
    sans les métadonnées de séance, voir `_respondents`). Utilisé partout où
    une mention brute (intervenant, répondant, auteur) peut en réalité
    désigner plusieurs personnes ou porter un rôle collé au nom — jamais un
    simple split() naïf."""
    names = []
    for part in _RESP_SPLIT.split(raw or ""):
        c = _clean(part).strip()
        if not c:
            continue
        toks = [t for t in c.split() if not _is_role_token(t)]
        if toks:
            names.append(" ".join(toks))
    return names


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
            bff = seance.get("bourgmestre_ff")
            if bff and _looks_like_name(bff):
                names.append(bff)
        elif "bourgmestre" in low or "burgemeester" in low:
            b = seance.get("bourgmestre")
            if b and _looks_like_name(b):
                names.append(b)
    return names


# ── Types de points → libellés d'affichage ───────────────────────────────────
_TYPE_LABEL = {
    "question_orale": "Question orale",
    "demande_habitant": "Demande",
    "motion": "Motion",
    "video": "Débat filmé",
}


def _first_named_intervenant(interv: list):
    """1er intervenant clairement attribuable à UNE personne : ignore les
    entrées de rôle pur (« Secrétaire communal ») et les mentions composées
    (« MM. Bouhjar et El Arnouki », ambiguës quant à qui est réellement le
    1er intervenant) plutôt que de les prendre à la lettre."""
    for raw in interv or []:
        names = _split_person_names(raw)
        if len(names) == 1:
            return names[0]
    return None


def _author_of(point: dict):
    """Nom de l'auteur·e d'un point PV selon son type (None si non attribuable)."""
    typ = point.get("type")
    title = point.get("titre") or ""
    interv = point.get("intervenants") or []
    if typ == "question_orale":
        return _first_named_intervenant(interv)
    if typ == "demande_habitant":
        m = _AUTHOR_IN_TITLE.search(title)
        return m.group(1) if m else _first_named_intervenant(interv)
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
    video = _load_video()
    pairs = _build_name_registry(pv, video)
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
                    k = _key(author, pairs)
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
                k = _key(name, pairs)
                if not k:
                    continue
                add_variant(k, name)
                people[k]["repond"].append({
                    "date": date,
                    "sp": p.get("sp") or 0,
                    "titre": p.get("titre") or "",
                    "url": meta.get("source_url"),
                })

    for s in video:
        date = s.get("date")
        for p in s.get("points", []):
            author = p.get("auteur")
            if not author or _is_non_person_video_author(author):
                continue
            k = _key(author, pairs)
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

    # Enrichissement du nom d'affichage UNIQUEMENT : certain·e·s élu·e·s
    # n'apparaissent en tant qu'auteur·e/répondant·e (les deux seuls rôles
    # comptabilisés) que sous leur seul nom de famille (ex. « M. Denys » en
    # répondant), alors qu'un prénom complet existe dans la liste des
    # intervenant·e·s du même point. On récupère ces variantes plus
    # complètes pour l'affichage, sans jamais créer de fiche ni compter une
    # intervention supplémentaire (une entrée en intervenant·e n'est pas en
    # soi une intervention attribuable, cf. docstring du module).
    for s in pv:
        for p in s.get("points", []):
            for interv in (p.get("intervenants") or []):
                # Une entrée d'intervenant·e peut elle-même être composée
                # (« MM. Bouhjar et El Arnouki ») ou porter un rôle collé au
                # nom (« Bourgmestre Audrey Henry ») : on la découpe comme
                # n'importe quelle autre mention brute avant utilisation,
                # jamais telle quelle (sinon le fragment de rôle/la mention
                # à plusieurs personnes pollue le nom affiché de qui que ce
                # soit dont la clé correspond au dernier mot).
                for name in _split_person_names(interv):
                    k = _key(name, pairs)
                    if k in people:
                        add_variant(k, name)

    index = {}
    for k, d in people.items():
        nom = _DISPLAY_NAME_OVERRIDES.get(k) or _titlecase(_best_display_variant(d["variants"], k))
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
