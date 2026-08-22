"""Tests des utilitaires du pipeline d'extraction (`pv_extraction_pipeline.py`) :
déduction de date depuis le nom de fichier, normalisation du numéro de point
(sp) que Claude renvoie tantôt en texte, tantôt en entier, et libération
mémoire par page lors de l'extraction du texte du PDF.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pv_extraction_pipeline import extract_pdf_metadata, extract_text_from_pdf, _coerce_sp, _sp_key


# ── extract_pdf_metadata : date depuis le NOM de fichier ────────────────────
@pytest.mark.parametrize("nom, date", [
    ("pv_conseil_2023.02.15_sp.pdf", "2023-02-15"),
    ("PV-20210630.pdf", "2021-06-30"),
    ("CC_25032020_1830.pdf", "2020-03-25"),          # JJMMAAAA
    ("2019-05-15_conseil.pdf", "2019-05-15"),
])
def test_date_depuis_nom(nom, date):
    assert extract_pdf_metadata(Path(nom))["date"] == date


def test_seance_id_derive_de_la_date():
    meta = extract_pdf_metadata(Path("pv_conseil_2022.01.26.pdf"))
    assert meta["seance_id"] == "PV-2022-01-26"


@pytest.mark.parametrize("nom", [
    "ordre_du_jour_sans_date.pdf",
    "document.pdf",
])
def test_date_absente_retourne_none(nom):
    assert extract_pdf_metadata(Path(nom))["date"] is None


def test_date_invalide_rejetee():
    # mois 18 impossible → pas de date déduite (validation stricte)
    assert extract_pdf_metadata(Path("pv_2020.18.40.pdf"))["date"] is None


# ── _coerce_sp : sp texte → entier ──────────────────────────────────────────
@pytest.mark.parametrize("entree, sp", [
    ({"sp": "12"}, 12),
    ({"sp": "12 bis"}, 12),
    ({"sp": 7}, 7),
])
def test_coerce_sp(entree, sp):
    assert _coerce_sp(entree)["sp"] == sp


def test_coerce_sp_non_numerique_inchange():
    # « urgence » ne commence pas par un chiffre → laissé tel quel
    assert _coerce_sp({"sp": "urgence"})["sp"] == "urgence"


# ── _sp_key : tri robuste (entiers avant textes, pas de TypeError) ──────────
def test_sp_key_tri_mixte():
    points = [{"sp": 3}, {"sp": "bis"}, {"sp": 1}, {"sp": 10}]
    ordonnes = [p["sp"] for p in sorted(points, key=_sp_key)]
    assert ordonnes == [1, 3, 10, "bis"]


# ── extract_text_from_pdf : libère chaque page après usage ──────────────────
# pdfplumber construit tous les objets Page dès l'ouverture et met en cache
# leurs données de mise en page (chars/mots/rects) dès extract_text() — sans
# page.close(), la mémoire croît linéairement avec le nombre de pages jusqu'à
# la sortie du `with`. Sur un PV dense/bilingue, ça a suffi à faire tuer le
# process en production (RAM du tier gratuit Render dépassée en plein milieu
# de l'extraction). Vérifie que chaque page est bien fermée après usage.
def test_extract_text_from_pdf_closes_each_page():
    page1 = MagicMock()
    page1.extract_text.return_value = "Texte page 1"
    page2 = MagicMock()
    page2.extract_text.return_value = "Texte page 2"

    fake_pdf = MagicMock()
    fake_pdf.pages = [page1, page2]
    fake_pdf.__enter__.return_value = fake_pdf
    fake_pdf.__exit__.return_value = False

    with patch("pv_extraction_pipeline.pdfplumber.open", return_value=fake_pdf):
        pages = extract_text_from_pdf(Path("fake.pdf"))

    assert [p["text"] for p in pages] == ["Texte page 1", "Texte page 2"]
    assert [p["page_num"] for p in pages] == [1, 2]
    page1.close.assert_called_once()
    page2.close.assert_called_once()
