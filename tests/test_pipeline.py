"""Tests des utilitaires du pipeline d'extraction (`pv_extraction_pipeline.py`) :
déduction de date depuis le nom de fichier, et normalisation du numéro de point
(sp) que Claude renvoie tantôt en texte, tantôt en entier.
"""
from pathlib import Path

import pytest

from pv_extraction_pipeline import extract_pdf_metadata, _coerce_sp, _sp_key


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
