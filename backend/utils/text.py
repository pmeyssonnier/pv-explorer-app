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
