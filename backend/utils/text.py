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
