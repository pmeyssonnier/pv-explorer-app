"""Normalisation des noms de personnes : civilités, casse, clé d'identité
(rapproche les variantes d'une même personne), alias/coquilles connus,
découpage d'une mention composée en noms individuels.

Aucune connaissance de la structure d'un PV/d'une séance ici — uniquement du
texte brut (une mention d'intervenant·e, un nom de vidéo…) vers un ou
plusieurs noms de personne normalisés. La résolution "qui a déposé ce point ?"
/ "qui y a répondu ?" est un problème différent, traité dans `attribution.py`.
"""
import re
import unicodedata

import lexique_store

# ── Normalisation des noms ───────────────────────────────────────────────────
# Civilités en tête de nom (répétables : « M. le … »).
_CIV = re.compile(
    r"^(?:MM\.?|Messieurs|M\.?|Mme\.?|Mr\.?|Monsieur|Madame|Mesdames|Mlle|Dr\.?|"
    r"de\s+heer|Mevrouw|Mevr\.?|Dhr\.?|Mijnheer)\s+",
    re.I,
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
# Séparateurs d'une mention composée (« X et Y », « X puis Y »…) — utilisé
# aussi bien pour un « repondant » composé que pour tout autre champ pouvant
# désigner plusieurs personnes.
_RESP_SPLIT = re.compile(r"\s+et\s+|\s+en\s+|\s+puis\s+|\s+ensuite\s+|&|,|;|/|\+", re.I)

# Coquilles ponctuelles observées dans le chapitrage (ex. « Ouazrhrari » pour
# « Ouazrhari ») : la clé fautive est normalisée vers la clé correcte, pour
# éviter une fiche dupliquée. Un simple réordonnancement de mots (couvert par
# le registre de paires (prénom, nom) ci-dessous) ne peut pas détecter une
# faute de frappe DANS un mot — d'où cette liste, à compléter au cas par cas.
# Sert aussi pour un PRÉNOM SEUL capté comme clé (ex. « Yvan » au lieu de
# « Yvan de Beauffort » — la regex _AUTHOR_IN_TITLE d'attribution.py s'arrête
# avant la particule minuscule « de », et aucun intervenant du point ne
# commence par « Yvan » pour permettre le repli habituel vers la forme
# complète, voir _author_from_text) : même clé finale que le nom complet.
_KEY_ALIASES = {"ouazrhrari": "ouazrhari", "erlay": "eraly", "yvan": "beauffort"}

# Prénoms connus mais absents de toutes les sources PV/vidéo (la personne
# n'y est jamais mentionnée que par son seul nom de famille) : complétés
# manuellement, clé = _key() du nom de famille.
_DISPLAY_NAME_OVERRIDES = {
    "malingreau": "Alain Malingreau",
    "smeysters": "Christine Smeysters",
    "sobieski": "Christine Sobieski",
    "essaidi": "Tamimount Essaidi",
    "hemamou": "Afaf Hemamou",
}

# Homonymes détectés dans le corpus : deux personnes RÉELLES et DISTINCTES
# qui partagent un nom de famille, que _key() (basé sur le seul nom de
# famille) fusionnerait sinon en une fiche unique — la moins mentionnée
# hériterait alors du nom affiché de l'autre. Ex. Marie Nyssens, autrice
# d'une question écrite isolée (2020), n'a aucun lien avec Clotilde
# Nyssens, conseillère communale citée des dizaines de fois dans les PV.
# Emel Köse (conseillère depuis 2019) et Saït Köse (échevin 2012-2018, puis
# conseiller) siègent SIMULTANÉMENT — les deux nommé·e·s côte à côte à
# l'installation du conseil du 01/12/2024 (« KÖSE Emel » / « KÖSE Saït » sur
# le même point) : preuve directe que ce sont deux personnes, pas une
# mention à deux dates différentes de la même.
# Clé = ENSEMBLE des mots du nom complet nettoyé (accents/casse normalisés,
# ordre indifférent — « Marie Nyssens » et « Nyssens Marie », vues toutes
# deux dans le corpus, doivent matcher pareil, voir _homonym_override) ;
# valeur = clé d'identité dédiée (jamais celle du seul nom de famille).
# Une mention SANS prénom (« Nyssens », « Köse » seul) reste sur la clé par
# défaut (la personne la plus citée) : aucun signal pour la désambiguïser,
# et on ne devine jamais — seules les mentions portant le prénom complet
# migrent vers la fiche dédiée.
_HOMONYM_KEY_OVERRIDES = {
    frozenset({"marie", "nyssens"}): "nyssens_marie",
    frozenset({"emel", "kose"}): "kose_emel",
}


def _homonym_override(cleaned: str) -> str | None:
    toks = frozenset(_norm_tok(t) for t in re.split(r"\s+", cleaned) if t)
    return _HOMONYM_KEY_OVERRIDES.get(toks)

# Organismes captés à tort comme « auteur » sur des points de convention/
# partenariat du chapitrage vidéo (le contre-signataire, pas un·e citoyen·ne
# intervenant·e) : jamais une personne, à exclure de l'agrégation.
_NON_PERSON_VIDEO_AUTHORS = {"clad", "greentech vzw", "gemeente schaarbeek"}

# Agent·e·s communaux·ales (services administratifs) cité·e·s comme
# « répondant » sur des points de pure correspondance/routine (accusés de
# réception, transmission de documents RH…) — jamais un membre élu du
# Collège répondant politiquement. Repéré en audit : le 24/02/2016, une
# série de points « Correspondance » (rubriques Human Resources/
# Infrastructuur/Openbaar onderwijs…) porte le nom de l'agent·e traitant·e
# comme répondant, ce qui leur créait à tort une fiche « élu·e » (rôle
# Collège inventé) dans l'onglet Par élu·e. Clé = _key() du nom complet.
_NON_ELU_REPONDANTS = {
    "laurence", "pire", "steinbach", "reepingen", "velghe",
    "chantraine", "dhuyvetter", "luc", "martin",
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
    observées sans ambiguïté ailleurs dans le corpus (voir
    `attribution._build_name_registry`) permet de reconnaître l'ordre
    inversé et de retrouver le vrai nom de famille, quel que soit le mot
    qui le porte dans cette mention-ci. On ne s'appuie jamais sur « ce mot
    est un nom de famille connu » tout seul : un même mot peut être le nom
    de famille d'une personne et le prénom d'une autre (ex. « Bernard » :
    nom de famille d'Axel Bernard, prénom du bourgmestre Bernard Clerfayt)
    — seule la paire complète est fiable.
    """
    cleaned = _clean(name)
    override = _homonym_override(cleaned)
    if override:
        return override
    toks = [t for t in re.split(r"\s+", cleaned) if t]
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
    # Lexique éditable (alias ajoutés à chaud) prioritaire sur les alias en dur.
    return lexique_store.person_aliases().get(k) or _KEY_ALIASES.get(k, k)


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
    sans les métadonnées de séance, voir `attribution._respondents`). Utilisé
    partout où une mention brute (intervenant, répondant, auteur) peut en
    réalité désigner plusieurs personnes ou porter un rôle collé au nom —
    jamais un simple split() naïf."""
    names = []
    for part in _RESP_SPLIT.split(raw or ""):
        c = _clean(part).strip()
        if not c:
            continue
        toks = [t for t in c.split() if not _is_role_token(t)]
        if toks:
            names.append(" ".join(toks))
    return names


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


def _resolve_display_name(name: str, pairs: set, nom_by_key: dict):
    """Nom canonique d'affichage pour une mention brute donnée (via le
    registre issu de `registry._build_all`), avec repli sur une casse
    homogène si la personne n'a pas de fiche connue."""
    if not name:
        return None
    k = _key(name, pairs)
    nom = nom_by_key.get(k)
    if nom:
        return nom
    return _titlecase(_clean(name)) or None
