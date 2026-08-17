"""Tests de normalisation de texte : `_strip_accents` et `_canon_theme`
(fusion des doublons singulier/pluriel des thématiques).
"""
import pytest

from utils.text import _strip_accents, _canon_theme


@pytest.mark.parametrize("s, attendu", [
    ("éducation", "education"),
    ("Propreté", "Proprete"),
    ("château", "chateau"),
    ("sans accent", "sans accent"),
])
def test_strip_accents(s, attendu):
    assert _strip_accents(s) == attendu


# ── Le cœur : deux tags différents → un même libellé canonique ──────────────
def test_canon_singulier_pluriel_fusionnent():
    assert _canon_theme("marche_public") == _canon_theme("marches_publics")


def test_canon_enseignement_vers_education():
    assert _canon_theme("enseignement") == "education"
    assert _canon_theme("education") == "education"


@pytest.mark.parametrize("tag, attendu", [
    ("marches_publics", "marche public"),
    ("marche_public", "marche public"),
    ("fournitures", "fourniture"),
    ("subventions", "subvention"),
    ("finances", "finance"),
    ("asbl", "asbl"),                       # < 5 lettres → pas de dé-pluralisation
    ("environnement", "environnement"),     # se termine par « t », inchangé
])
def test_canon_theme(tag, attendu):
    assert _canon_theme(tag) == attendu


def test_canon_ne_coupe_pas_les_mots_courts():
    # Mot de 4 lettres finissant par « s » : garde-fou longueur > 4 → intact.
    assert _canon_theme("avis") == "avis"
