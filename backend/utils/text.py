"""Normalisation de texte réutilisable : suppression des accents et canonisation
des tags de thématiques (fusion des doublons singulier/pluriel).
"""
import re
import unicodedata


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


# Fusions de thèmes après singularisation (mêmes sujets, tags différents).
_THEME_CANON = {"enseignement": "education"}


def _canon_theme(theme: str) -> str:
    """Normalise un tag `thematiques` en libellé canonique : accents retirés,
    underscores → espaces, singularisation simple (marches_publics /
    marche_public → « marche public », fournitures → « fourniture »). Fusionne
    les doublons singulier/pluriel qui gonflent artificiellement le comptage."""
    s = re.sub(r"\s+", " ", _strip_accents(theme.lower()).replace("_", " ")).strip()
    toks = []
    for w in s.split(" "):
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        toks.append(w)
    s = " ".join(toks)
    return _THEME_CANON.get(s, s)


def _thematique_label(t: str) -> str:
    """Étiquette d'affichage d'une thématique (slug interne, ex.
    « transports_publics »). Passe par _canon_theme — la même fusion
    singulier/pluriel que l'onglet Statistiques (services.statistics.
    compute_stats) — pour qu'un même tag s'affiche IDENTIQUEMENT dans toutes
    les vues (Statistiques, Séances, Par élu·e, sources des réponses)."""
    s = _canon_theme((t or "").replace("-", " "))
    return s[:1].upper() + s[1:] if s else s


# Libellés d'affichage homogènes pour le champ « decision » du PV, dont la
# graphie brute varie (casse, accents, coquilles ponctuelles — ex. « PRENDS
# POUR INFORMATION », « PRENDRE ACTE »). Clé = texte sans accents/minuscule.
# Partagé par services.seances._decision_summary (vote COMPLET, lu depuis le
# JSON) et _decision_status ci-dessous (vote_type SEUL, lu depuis Pinecone).
_DECISION_LABELS = {
    "approuve": "Approuvé",
    "decide": "Décidé",
    "decidé": "Décidé",
    "debat": "Débat",
    "reporte": "Reporté",
    "retire": "Retiré",
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


def _decision_status(decision: str, vote_type: str = None) -> str:
    """Statut lisible d'une décision (label normalisé + issue du vote quand
    connue) à partir des SEULES métadonnées disponibles côté recherche
    vectorielle (services.rag) : Pinecone n'indexe que le type de vote
    (« unanimite »/« vote_nominal »/« reporte »), pas le détail pour/contre/
    abstentions — voir index_pv.py. Repli honnête : contrairement à
    services.seances._decision_summary (vote complet, lu depuis le JSON), on
    n'invente jamais de décompte pour un vote nominal — juste le label."""
    d = (decision or "").strip()
    if not d:
        return ""
    norm = _strip_accents(d).lower()
    label = _DECISION_LABELS.get(norm) or (d[:1].upper() + d[1:].lower())
    if vote_type == "unanimite":
        return f"{label} à l'unanimité"
    return label
