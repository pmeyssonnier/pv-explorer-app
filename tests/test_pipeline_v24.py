"""Tests des greffes v2.4 du pipeline d'extraction (fonctions PURES, sans API) :
normalisation, parsing montant, traçabilité page, complétude, date-repli.
Verrouille en CI les comportements testés « en isolation » lors du merge.
"""
import pytest

from pv_extraction_pipeline import (
    _coerce_amount, normalize_point, _fix_point_pages,
    expected_sp_from_pages, verify_completeness, extract_seance_date_from_text,
)
from reextract_targeted import targets_from_audit


# ── targets_from_audit : sélection des séances à re-extraire ────────────────
def test_targets_from_audit_filtre_seuil():
    report = [
        {"date": "2016-11-30", "status": "incomplet", "missing_sp": list(range(12, 27))},
        {"date": "2022-12-21", "status": "incomplet", "missing_sp": [87]},  # off-by-one
        {"date": "2020-01-29", "status": "ok"},
    ]
    assert targets_from_audit(report, min_missing=1) == ["2016-11-30", "2022-12-21"]
    # min_missing=2 écarte le off-by-one (souvent un faux positif regex)
    assert targets_from_audit(report, min_missing=2) == ["2016-11-30"]


# ── _coerce_amount (dont la régression « TVAC ») ────────────────────────────
@pytest.mark.parametrize("raw, val", [
    ("65.000 € TVAC", 65000.0),        # suffixe texte ignoré (le bug corrigé)
    ("1.234,56 € HTVA", 1234.56),
    ("65.000 €", 65000.0),
    ("1 234,50", 1234.50),             # espace comme séparateur de milliers
    (65000, 65000.0),
    (12.5, 12.5),
    (None, None),
    ("", None),
    ("gratuit", None),                 # aucun chiffre → None
])
def test_coerce_amount(raw, val):
    assert _coerce_amount(raw) == val


# ── normalize_point : conserve `page` (point de fusion) + normalise ─────────
def test_normalize_point_preserve_page_and_normalize():
    p = normalize_point({"sp": 12, "page": 7, "titre": "  Objet  ",
                         "montant_eur": "65.000 € TVAC", "urgence": "oui"})
    assert p["sp"] == 12
    assert p["page"] == 7                # ← sinon la traçabilité serait perdue
    assert p["montant_eur"] == 65000.0
    assert p["titre"] == "Objet"
    assert p["urgence"] is True


@pytest.mark.parametrize("bad", [{"titre": "sans sp"}, "pas un dict", None])
def test_normalize_point_rejette_sans_sp(bad):
    assert normalize_point(bad) is None


# ── _fix_point_pages : bornage à l'intervalle réel du chunk ─────────────────
def test_fix_point_pages_bounds_to_chunk():
    chunk = [{"page_num": 5}, {"page_num": 6}, {"page_num": 7}]
    pts = [{"page": 6}, {"page": 99}, {"page": None}, {"page": "Page 5"}]
    _fix_point_pages(pts, chunk)
    # 6 dans l'intervalle ; 99 hors-borne → repli 1re page ; None → repli ; "Page 5" → 5
    assert [p["page"] for p in pts] == [6, 5, 5, 5]


# ── expected_sp_from_pages : ancre déterministe « SP n.- » ──────────────────
def test_expected_sp_from_pages():
    pages = [{"page_num": 1, "text": "SP 1.- Objet A ... SP 2.- Objet B"},
             {"page_num": 2, "text": "SP 3.- Objet C"}]
    assert expected_sp_from_pages(pages) == {1: 1, 2: 1, 3: 2}


# ── verify_completeness : détecte ce que le LLM a raté ──────────────────────
def test_verify_completeness_detecte_manquant():
    pages = [{"page_num": 1, "text": "SP 1.- A SP 2.- B SP 3.- C"}]
    r = verify_completeness(pages, [{"sp": 1}, {"sp": 3}])
    assert r["missing_sp"] == [2]
    assert r["missing_pages"] == [1]
    assert r["ok"] is False


def test_verify_completeness_complet():
    pages = [{"page_num": 1, "text": "SP 1.- A SP 2.- B"}]
    assert verify_completeness(pages, [{"sp": 1}, {"sp": 2}])["ok"] is True


# ── extract_seance_date_from_text : FR/NL + accents/espaces perdus ──────────
@pytest.mark.parametrize("text, date", [
    ("Séance du 20 septembre 2023", "2023-09-20"),
    ("SEANCE DU20 SEPTEMBRE 2023", "2023-09-20"),   # accents + espace perdus
    ("Vergadering van 15 januari 2024", "2024-01-15"),
    ("Séance du 05/03/2020 à 18h", "2020-03-05"),
    ("aucune date ici", None),
])
def test_extract_date_from_text(text, date):
    assert extract_seance_date_from_text(text) == date
