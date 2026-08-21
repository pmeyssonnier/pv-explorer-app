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
import difflib
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
# Auteur mentionné dans le titre/résumé d'une demande/motion (« Motion de M.
# Axel Bernard », mais aussi « Demande M. Demirhan » sans « de » — la
# tournure varie selon les séances, d'où le « de/du » optionnel ci-dessous).
# Lettre capitale de tête : classe explicite plutôt que [A-ZÀ-Ÿ]/[À-Ÿ], qui
# inclut par erreur des minuscules accentuées (le bloc Latin-1 Supplement
# n'est pas trié majuscules/minuscules à part — ex. « é » U+00E9 tombe dans
# la plage À(00C0)-Ÿ(0178) ci-dessus), et faisait capter un mot lambda après
# « Demande » employé comme verbe en milieu de phrase (« Demande également
# aux autorités... ») plutôt que la tournure « Demande de/M./Mme X ».
_UPPER_ACCENTS = "ÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸÑŒ"
_AUTHOR_IN_TITLE = re.compile(
    r"(?:Demande|Motion|Interpellation|Verzoek|Motie)\s+(?:d[eu']?\s+)?"
    r"(?:M\.?|Mme|Monsieur|Madame|de heer|Mevrouw)?\s*"
    rf"([A-Z{_UPPER_ACCENTS}][\wÀ-ÿ'’-]+(?:\s+[A-Z{_UPPER_ACCENTS}][\wÀ-ÿ'’-]+){{0,2}})"
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
_KEY_ALIASES = {"ouazrhrari": "ouazrhari", "erlay": "eraly"}

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
            # « Bourgmestre » seul (sans mention « ff ») : normalement le
            # titulaire, mais si la séance n'en a aucun d'enregistré (champ
            # absent), c'est que c'était le/la bourgmestre f.f. qui présidait
            # — on retombe donc sur ce nom plutôt que de perdre la mention.
            b = seance.get("bourgmestre") or seance.get("bourgmestre_ff")
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


def _author_from_text(text: str, interv: list):
    """Cherche « Demande/Motion de M./Mme X » dans un texte donné (titre ou
    résumé). Si le nom capté s'arrête avant une particule en minuscule (la
    regex ne capture que des mots capitalisés, ex. « Yvan » au lieu de
    « Yvan de Beauffort ») et qu'un·e intervenant·e listé·e commence par ce
    nom tronqué, on préfère la forme complète — plus fiable qu'un prénom
    seul, sans pour autant deviner l'auteur·e à partir des intervenant·e·s."""
    m = _AUTHOR_IN_TITLE.search(text or "")
    if not m:
        return None
    name = m.group(1)
    low = name.lower()
    for iv in interv or []:
        if iv.lower() != low and iv.lower().startswith(low):
            return iv
    return name


def _author_of(point: dict):
    """Nom de l'auteur·e d'un point PV selon son type (None si non attribuable).

    Le nom de l'auteur·e est parfois donné dans le titre (« Demande de Mme
    X… »), parfois seulement dans le résumé (« resume ») quand le titre
    n'en porte pas trace — les deux champs sont donc vérifiés."""
    typ = point.get("type")
    title = point.get("titre") or ""
    resume = point.get("resume") or ""
    interv = point.get("intervenants") or []
    if typ == "question_orale":
        return _first_named_intervenant(interv)
    if typ == "demande_habitant":
        name = _author_from_text(title, interv) or _author_from_text(resume, interv)
        return name or _first_named_intervenant(interv)
    if typ == "motion":
        # Jamais de repli sur les intervenant·e·s (souvent collectif) : seul
        # un texte le nommant explicitement (titre OU résumé) vaut attribution.
        return _author_from_text(title, interv) or _author_from_text(resume, interv)
    return None


# Attribution manuelle ponctuelle, vérifiée individuellement (PV + transcript
# vidéo), pour des points normalement jamais attribués automatiquement — type
# "point_normal" (administratif/collectif) : le champ « intervenants » y
# mélange sans distinction citoyen·ne·s/conseiller·ère·s à l'origine de la
# discussion et membres du Collège qui la président ou y répondent, donc pas
# de règle générale fiable (contrairement à question_orale/demande_habitant/
# motion, voir _author_of) — seuls des cas confirmés individuellement sont
# ajoutés ici. Clé = (date, sp).
_MANUAL_AUTHOR_OVERRIDES = {
    ("2026-04-22", 12): "Matthieu Degrez",
    ("2026-04-22", 13): "Georges Verzin",
    ("2026-04-22", 15): "Quentin Van den Hove",
}


def _point_author(point: dict, pairs: set, date: str | None = None):
    """Auteur·e d'un point PV + sa clé, si attribuable à UNE personne (jamais
    un mot de rôle seul comme dernier mot, ex. « Secrétaire communal »).
    Factorisé pour être partagé entre l'agrégation par personne (_build_all)
    et la vue par séance (seance_detail), qui doivent s'accorder sur qui est
    le/la demandeur·se d'un point donné."""
    author = _MANUAL_AUTHOR_OVERRIDES.get((date, point.get("sp"))) or _author_of(point)
    if not author:
        return None, None
    last = author.split()[-1] if author.split() else ""
    if not last or _is_role_token(last):
        return author, None
    return author, _key(author, pairs)


# ── Construction de l'index (cache par mtime des deux fichiers sources) ───────
_cache = {"sig": None, "index": None, "pairs": None, "nom_by_key": None}


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
    """Étiquette de rôle dominante, dérivée de l'activité (indicatif).

    Le champ "répondant" d'un point n'est renseigné que par le membre du
    Collège qui a répondu ; une personne qui répond au moins une fois et
    ne dépose jamais de point est donc, structurellement, un·e échevin·e
    ou le bourgmestre, même avec peu de séances (ex. mandat écourté).
    """
    if n_repond > 0 and n_depose == 0:
        return "college"
    if n_repond >= 8 and n_repond > n_depose:
        return "college"      # échevin·e / bourgmestre (répond en séance)
    return "conseiller"       # conseiller·ère (dépose des points)


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", _strip_accents(t or "").lower())


def _match_pv_point(video_titre: str, candidates: list, threshold: float = 0.35) -> dict | None:
    """Retrouve, parmi des points PV candidats, celui qui correspond au point
    vidéo (même sujet) — pour fusionner en UNE intervention plutôt que d'en
    afficher deux (« Demande »/« Motion » + « Débat filmé ») pour le même
    point, quand la séance a été filmée ET chapitrée. Les titres PV sont
    souvent plus courts que les titres de chapitrage vidéo (qui ajoutent
    « Demande de M./Mme X » + la traduction NL) : une simple inclusion de
    chaîne suffit la plupart du temps ; en cas d'ambiguïté, on utilise la
    similarité textuelle avec un seuil prudent — jamais de fusion à
    l'aveugle (mieux vaut deux entrées séparées qu'une fusion fausse).

    `threshold` doit être plus élevé quand `candidates` n'est pas déjà
    restreint à la même personne (chapitres collectifs sans auteur·e
    individuel·le, comparés à TOUS les points de la séance — bassin de
    candidats bien plus large, donc plus de risque de score élevé fortuit) :
    voir seance_detail, validé empiriquement à 0.6 sur le corpus réel."""
    if len(candidates) == 1:
        return candidates[0]
    vn = _norm_title(video_titre)
    contains = []
    for c in candidates:
        cn = _norm_title(c["titre"])
        if cn and (cn in vn or vn in cn):
            contains.append(c)
    if len(contains) == 1:
        return contains[0]
    pool = contains if contains else candidates
    best = max(pool, key=lambda c: difflib.SequenceMatcher(None, vn, _norm_title(c["titre"])).ratio())
    score = difflib.SequenceMatcher(None, vn, _norm_title(best["titre"])).ratio()
    return best if score >= threshold else None


def _build_all():
    """Construit l'index par personne + le registre de noms (pairs) + la map
    clé→nom canonique, en une seule passe sur les deux fichiers sources —
    partagés par plusieurs vues (/elu/{key} ET /seance/{date}) sans recalcul."""
    pv = load_db().get("seances", [])
    video = _load_video()
    pairs = _build_name_registry(pv, video)
    people = defaultdict(lambda: {"variants": defaultdict(int), "depose": [], "repond": []})

    def add_variant(k, name):
        people[k]["variants"][_clean(name)] += 1

    pdf_by_date = {}
    session_map = video_session_map()
    # (clé personne, date) -> entrées PV « déposées » de ce jour-là, pour
    # retrouver depuis la boucle vidéo (ci-dessous) le point PV correspondant
    # à un point de chapitrage vidéo, et les fusionner en une seule
    # intervention (voir _match_pv_point).
    pv_lookup = defaultdict(list)

    for s in pv:
        meta = s.get("seance", {}) or {}
        date = meta.get("date")
        pdf_by_date[date] = meta.get("source_url")
        for p in s.get("points", []):
            author, author_key = _point_author(p, pairs, date)
            if author_key:
                add_variant(author_key, author)

            # Noms des répondant·e·s résolus une seule fois (rôle seul type
            # « Bourgmestre » déjà traduit en nom via _respondents) et
            # réutilisés à la fois pour l'agrégation "repond" et pour
            # l'affichage "repondant" côté "depose" — canonisés en fin de
            # fonction (voir nom_by_key) pour un affichage homogène (casse,
            # ordre prénom/nom, nom complet) plutôt que le texte brut du PV.
            resp_raw = p.get("repondant")
            resp_keys = []
            for name in _respondents(resp_raw, meta):
                k = _key(name, pairs)
                if not k:
                    continue
                add_variant(k, name)
                resp_keys.append(k)
                people[k]["repond"].append({
                    "date": date,
                    "sp": p.get("sp") or 0,
                    "titre": p.get("titre") or "",
                    "url": meta.get("source_url"),
                    "demandeur_key": author_key,
                })

            if author_key:
                entry = {
                    "date": date,
                    "sp": p.get("sp") or 0,
                    "type": p.get("type"),
                    "titre": p.get("titre") or "",
                    "repondant_keys": resp_keys,
                    # Repli si le rôle mentionné n'a pas pu être résolu en
                    # nom de personne (ex. « Secrétaire communal », « Président ») :
                    # au moins une casse homogène plutôt que le texte brut du PV.
                    "repondant_fallback": None if resp_keys else (_titlecase(_clean(resp_raw or "")) or None),
                    "url": meta.get("source_url"),
                    "video_url": session_map.get(date),
                }
                people[author_key]["depose"].append(entry)
                pv_lookup[(author_key, date)].append(entry)

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
            titre = p.get("titre_fr") or p.get("titre") or ""
            deeplink = p.get("deeplink")
            candidates = pv_lookup.get((k, date))
            match = _match_pv_point(titre, candidates) if candidates else None
            if match is not None:
                # Même point que ce point PV (même personne, même date, même
                # sujet) : une seule intervention, avec le lien vidéo précis
                # (l'instant exact du point) plutôt que le lien de séance
                # générique — au lieu d'une 2e entrée « Débat filmé » pour
                # la même chose.
                if deeplink:
                    match["video_url"] = deeplink
                    match["video_precise"] = True
                continue
            people[k]["depose"].append({
                "date": date,
                "sp": 0,
                "type": "video",
                "titre": titre,
                "repondant": None,
                "url": deeplink,
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

    # Noms canoniques calculés UNE FOIS que toutes les variantes (dépôts,
    # réponses, enrichissement ci-dessus) sont connues pour tout le monde —
    # réutilisés ensuite pour résoudre "repondant"/"demandeur" en noms
    # complets et homogènes (même casse/ordre que la fiche de la personne),
    # plutôt que le texte brut du PV (ex. « ERALY » ou « VANHALEWYN VINCENT »).
    nom_by_key = {
        k: _DISPLAY_NAME_OVERRIDES.get(k) or _titlecase(_best_display_variant(d["variants"], k))
        for k, d in people.items()
    }

    index = {}
    for k, d in people.items():
        for entry in d["depose"]:
            keys = entry.pop("repondant_keys", None)
            fallback = entry.pop("repondant_fallback", None)
            if keys is not None:
                names = list(dict.fromkeys(nom_by_key[kk] for kk in keys if kk in nom_by_key))
                entry["repondant"] = " et ".join(names) if names else fallback
        for entry in d["repond"]:
            dk = entry.pop("demandeur_key", None)
            entry["demandeur"] = nom_by_key.get(dk) if dk else None
        d["depose"].sort(key=lambda it: (it["date"] or "", it["sp"]), reverse=True)
        d["repond"].sort(key=lambda it: (it["date"] or "", it["sp"]), reverse=True)
        index[k] = {
            "key": k,
            "nom": nom_by_key[k],
            "role": _role_of(len(d["depose"]), len(d["repond"])),
            "depose": d["depose"],
            "repond": d["repond"],
        }
    return index, pairs, nom_by_key


def _ensure_cache():
    sig = _sig()
    if _cache["sig"] != sig:
        index, pairs, nom_by_key = _build_all()
        _cache["sig"] = sig
        _cache["index"] = index
        _cache["pairs"] = pairs
        _cache["nom_by_key"] = nom_by_key


def _index() -> dict:
    _ensure_cache()
    return _cache["index"]


def _pairs() -> set:
    _ensure_cache()
    return _cache["pairs"]


def _nom_by_key() -> dict:
    _ensure_cache()
    return _cache["nom_by_key"]


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
            "video_precise": bool(it.get("video_precise")),
        }

    def _fmt_repond(it):
        return {
            "date": it["date"],
            "sp": it["sp"],
            "titre": it["titre"],
            "url": it.get("url"),
            "demandeur": it.get("demandeur"),
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


# ── Vue par séance (onglet « Séances ») ─────────────────────────────────────
# Complémentaire à la vue par élu·e : au lieu d'agréger toutes les
# interventions d'UNE personne, liste TOUS les points d'UN PV donné (y
# compris les points collectifs/administratifs sans auteur·e individuel·le
# identifiable), avec demandeur/répondant résolus via le même registre de
# noms canoniques (voir _build_all/_nom_by_key) pour un affichage homogène.

def _resolve_display_name(name: str, pairs: set, nom_by_key: dict):
    if not name:
        return None
    k = _key(name, pairs)
    nom = nom_by_key.get(k)
    if nom:
        return nom
    return _titlecase(_clean(name)) or None


def _is_reportee(decision) -> bool:
    """Point renvoyé à une séance ultérieure (« REPORTÉ ») : jamais débattu
    ce jour-là, donc jamais de répondant·e ni de débat filmé à en attendre."""
    return _strip_accents(decision or "").strip().lower().startswith("report")


def _thematique_label(t: str) -> str:
    """Étiquette d'affichage d'une thématique (slug interne, ex.
    « transports_publics ») : espaces au lieu des soulignés, casse
    homogène. Plus de 4500 valeurs distinctes dans le corpus (souvent
    rares/spécifiques) : pas de dictionnaire de correction des accents
    (perdus dans les slugs), simple normalisation."""
    s = (t or "").replace("_", " ").replace("-", " ").strip()
    return s[:1].upper() + s[1:] if s else s


# Libellés d'affichage homogènes pour le champ « decision » du PV, dont la
# graphie brute varie (casse, accents, coquilles ponctuelles — ex. « PRENDS
# POUR INFORMATION », « PRENDRE ACTE »). Clé = texte sans accents/minuscule.
_DECISION_LABELS = {
    "approuve": "Approuvé",
    "decide": "Décidé",
    "decidé": "Décidé",
    "debat": "Débat",
    "reporte": "Reporté",
    "prend acte": "Pris acte",
    "prendre acte": "Pris acte",
    "prend pour information": "Pris pour information",
    "prendre pour information": "Pris pour information",
    "prends pour information": "Pris pour information",
    "prises pour information": "Pris pour information",
    "arrete": "Arrêté",
    "nomme": "Nommé",
    "admet": "Admis",
    "beslist": "Décidé",
    "minute de silence": "Minute de silence",
}


def _decision_summary(decision, vote):
    """Résumé lisible de l'issue d'un point (décision + vote quand il y en a
    un), pour indiquer explicitement, selon chaque cas, pourquoi il n'y a
    par exemple ni répondant·e ni débat filmé à trouver (point voté sans
    discussion, reporté, pris pour information...) plutôt que de laisser
    croire à une recherche infructueuse. None si la décision est vide."""
    d = (decision or "").strip()
    if not d:
        return None
    norm = _strip_accents(d).lower()
    label = _DECISION_LABELS.get(norm)
    if not label:
        # Repli pour les variantes rares/coquilles non répertoriées
        # (ex. « PREND ACTE + DÉROGATION ART.12 ») : casse homogène.
        label = d[:1].upper() + d[1:].lower()
    vote = vote if isinstance(vote, dict) else {}
    vtype = vote.get("type")
    if vtype == "unanimite":
        return f"{label} à l'unanimité"
    if vtype == "vote_nominal":
        pour = vote.get("pour")
        contre = vote.get("contre") or 0
        abst = vote.get("abstentions") or 0
        bits = []
        if pour is not None:
            bits.append(f"{pour} pour")
        bits.append(f"{contre} contre")
        bits.append(f"{abst} abstention{'s' if abst != 1 else ''}")
        return f"{label} ({', '.join(bits)})"
    return label


def seances_list() -> list:
    """Liste des séances (PV), la plus récente en premier — pour la
    navigation par année dans l'onglet « Séances »."""
    pv = load_db().get("seances", [])
    session_map = video_session_map()
    out = []
    for s in pv:
        meta = s.get("seance", {}) or {}
        date = meta.get("date")
        if not date:
            continue
        out.append({
            "date": date,
            "n_points": len(s.get("points", [])),
            "url": meta.get("source_url"),
            "video_url": session_map.get(date),
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def seance_detail(date: str):
    """Détail d'une séance : tous les points du PV, avec pour chacun le
    demandeur·se (auteur·e, si attribuable), le/la répondant·e, et le lien
    vidéo — précis si un chapitre correspondant existe (même fusion
    point-à-point que _build_all, voir _match_pv_point), sinon le lien
    générique de séance. None si la date est inconnue."""
    pv = load_db().get("seances", [])
    date = (date or "").strip()
    seance = next((s for s in pv if (s.get("seance") or {}).get("date") == date), None)
    if not seance:
        return None
    meta = seance.get("seance", {}) or {}
    pairs = _pairs()
    nom_by_key = _nom_by_key()
    session_map = video_session_map()

    def resolve(name):
        return _resolve_display_name(name, pairs, nom_by_key)

    points = []
    for p in seance.get("points", []):
        author, author_key = _point_author(p, pairs, date)
        resp_names = _respondents(p.get("repondant"), meta)
        resp_resolved = list(dict.fromkeys(filter(None, (resolve(n) for n in resp_names))))
        repondant = " et ".join(resp_resolved) if resp_resolved else (
            _titlecase(_clean(p.get("repondant") or "")) or None
        )
        points.append({
            "sp": p.get("sp") or 0,
            "type": p.get("type"),
            "type_label": _TYPE_LABEL.get(p.get("type"), "Point"),
            "titre": p.get("titre") or "",
            "demandeur": resolve(author) if author_key else None,
            "repondant": repondant,
            "reporte": _is_reportee(p.get("decision")),
            "decision": _decision_summary(p.get("decision"), p.get("vote")),
            "thematiques": [_thematique_label(t) for t in (p.get("thematiques") or [])],
            "montant_eur": p.get("montant_eur"),
            "url": meta.get("source_url"),
            "video_url": session_map.get(date),
            "video_precise": False,
            "_author_key": author_key,
        })

    # Fusion avec le chapitrage vidéo de cette séance, point par point (même
    # logique que _build_all : un chapitre vidéo dont l'auteur·e et le sujet
    # correspondent à un point déjà listé remplace son lien générique par le
    # lien précis, plutôt que d'apparaître comme un point séparé).
    video_seance = next((s for s in _load_video() if s.get("date") == date), None)
    if video_seance:
        for vp in video_seance.get("points", []):
            vauthor = vp.get("auteur")
            titre = vp.get("titre_fr") or vp.get("titre") or ""
            deeplink = vp.get("deeplink")
            if vauthor and not _is_non_person_video_author(vauthor):
                vk = _key(vauthor, pairs)
                candidates = [pt for pt in points if pt["_author_key"] == vk] if vk else []
                match = _match_pv_point(titre, candidates) if candidates else None
            else:
                # Chapitre collectif (motion/point du collège sans auteur·e
                # individuel·le nommé·e, ex. « point_urgent ») : impossible
                # de restreindre par personne, donc comparaison au titre de
                # TOUS les points de la séance (hors chapitres déjà ajoutés
                # ci-dessous, jamais entre eux) — seuil bien plus élevé
                # (0.6 au lieu de 0.35) car le bassin de candidats est
                # nettement plus large, validé empiriquement sur le corpus
                # réel (au-dessus, correspondances toujours correctes ;
                # en-dessous, non fiable). Sans cette branche, ~59% des
                # chapitres vidéo (ceux sans auteur·e, ex. motions
                # collectives du Collège) étaient silencieusement omis de
                # cette vue plutôt que fusionnés ou même affichés à part.
                pv_points = [pt for pt in points if pt["type"] != "video"]
                match = _match_pv_point(titre, pv_points, threshold=0.6) if pv_points else None
            if match is not None:
                if deeplink:
                    match["video_url"] = deeplink
                    match["video_precise"] = True
            else:
                # Chapitre vidéo sans point PV correspondant identifié avec
                # confiance : affiché à part plutôt que silencieusement omis
                # (voir _match_pv_point — jamais de fusion à l'aveugle).
                points.append({
                    "sp": 0,
                    "type": "video",
                    "type_label": _TYPE_LABEL["video"],
                    "titre": titre,
                    "demandeur": resolve(vauthor),
                    "repondant": None,
                    "reporte": False,
                    "decision": None,
                    "thematiques": [],
                    "montant_eur": None,
                    "url": deeplink,
                    "video_url": vp.get("video_url") or video_seance.get("video_url"),
                    "video_precise": False,
                    "_author_key": None,
                })

    for pt in points:
        pt.pop("_author_key", None)
    points.sort(key=lambda x: x["sp"])

    video_url = (video_seance.get("video_url") if video_seance else None) or session_map.get(date)

    return {
        "date": date,
        "url": meta.get("source_url"),
        "video_url": video_url,
        "n_points": len(points),
        "points": points,
    }
