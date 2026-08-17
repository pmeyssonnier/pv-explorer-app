"""Tests du filtre temporel `_year_filter` — la fonction la plus sensible :
une mauvaise borne = des sources d'une autre année, donc une réponse
« crédible mais fausse ». Inclut la régression du faux positif « des ».
"""
import pytest

from utils.dates import _year_filter, _describe_year_filter


# ── Année exacte ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("q, year", [
    ("Quelles décisions en 2018 ?", 2018),
    ("Les subsides 2019", 2019),
    ("Sécurité en 2020", 2020),
    ("budget 2012", 2012),
])
def test_annee_exacte(q, year):
    assert _year_filter(q) == {"$eq": year}


# ── RÉGRESSION : « des » (article) ne doit PAS créer une borne >= ───────────
@pytest.mark.parametrize("q, year", [
    ("Quelles décisions des écoles en 2018 ?", 2018),
    ("Quelles aides des associations en 2020 ?", 2020),
    ("Le budget des travaux en 2015", 2015),
    ("La rénovation des trottoirs en 2017", 2017),
])
def test_des_article_reste_egalite(q, year):
    """« des écoles » (article partitif) ne doit jamais être lu comme « dès »."""
    assert _year_filter(q) == {"$eq": year}


# ── Borne basse ($gte) — mot-clé collé à l'année ────────────────────────────
@pytest.mark.parametrize("q, year", [
    ("budget propreté depuis 2015", 2015),
    ("subsides à partir de 2019", 2019),
    ("décisions après 2017", 2017),
    ("dès 2016 quels travaux ?", 2016),          # « dès » (accent) = vraie borne
    ("à compter de 2014", 2014),
])
def test_borne_basse(q, year):
    assert _year_filter(q) == {"$gte": year}


# ── Borne haute ($lte) ──────────────────────────────────────────────────────
@pytest.mark.parametrize("q, year", [
    ("les écoles avant 2016", 2016),
    ("projets jusqu'en 2018", 2018),
    ("jusqu'à 2020 quels marchés ?", 2020),
    ("jusqu'au 2013", 2013),
])
def test_borne_haute(q, year):
    assert _year_filter(q) == {"$lte": year}


# ── Fourchette ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("q, lo, hi", [
    ("entre 2015 et 2018 les subsides", 2015, 2018),
    ("de 2015 à 2018", 2015, 2018),
    ("évolution 2012 2020", 2012, 2020),
    ("entre 2020 et 2015", 2015, 2020),          # ordre inversé → normalisé
])
def test_fourchette(q, lo, hi):
    assert _year_filter(q) == {"$gte": lo, "$lte": hi}


# ── Absence d'année ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("q", [
    "Quelles décisions récentes ?",
    "rien sur les années",
    "le budget propreté",
    "",
])
def test_aucune_annee(q):
    assert _year_filter(q) is None


# ── Années hors plage plausible ignorées ────────────────────────────────────
def test_annee_hors_plage_ignoree():
    # 1999 et 2050 hors [2000, 2035] → aucune année retenue
    assert _year_filter("depuis 1999") is None


# ── _describe_year_filter : formulation française de la période ─────────────
@pytest.mark.parametrize("yf, texte", [
    ({"$eq": 2019}, "en 2019"),
    ({"$gte": 2015}, "depuis 2015"),
    ({"$lte": 2016}, "avant 2016"),
    ({"$gte": 2015, "$lte": 2018}, "entre 2015 et 2018"),
    ({"$gte": 2017, "$lte": 2017}, "en 2017"),
])
def test_describe_year_filter(yf, texte):
    assert _describe_year_filter(yf) == texte
