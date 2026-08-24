"""Tests de services.people.mandats — parsing des plages de mandats
déclaratives (elus_mandats.json) et classification du rôle par date.
Fonctions pures testées directement (_parse_ranges/_year_in_ranges) ;
role_at/mandats_for testées via un jeu de données monkeypatché (bypasse le
cache par mtime du vrai fichier, indépendant de son contenu réel). Les
vérifications sur les VRAIES données (élu·e·s réel·le·s, /elu/{key}) vivent
dans test_elus.py, à côté des autres tests utilisant la base réelle.
"""
import services.people.mandats as mandats


# ── _parse_ranges : "AAAA-AAAA" / "AAAA-présent", virgules, annotations ─────
def test_parse_ranges_single_closed_range():
    assert mandats._parse_ranges("2018-2024") == [(2018, 2024)]


def test_parse_ranges_open_ended_present():
    assert mandats._parse_ranges("2024-présent") == [(2024, None)]


def test_parse_ranges_multiple_comma_separated():
    assert mandats._parse_ranges("2012-2019, 2024-présent") == [(2012, 2019), (2024, None)]


def test_parse_ranges_strips_parenthetical_annotation():
    assert mandats._parse_ranges("2019-2024 (faisant fonction)") == [(2019, 2024)]
    assert mandats._parse_ranges("2025-présent (en titre; empêchée en 2026)") == [(2025, None)]


def test_parse_ranges_none_or_empty_string():
    assert mandats._parse_ranges(None) == []
    assert mandats._parse_ranges("") == []


def test_parse_ranges_ignores_unrecognized_segment():
    # Donnée déclarative externe : un segment malformé est ignoré, jamais
    # d'exception qui ferait planter tout le classement de rôle.
    assert mandats._parse_ranges("pas une date") == []
    assert mandats._parse_ranges("2018-2024, pas une date") == [(2018, 2024)]


# ── _year_in_ranges ──────────────────────────────────────────────────────────
def test_year_in_ranges_open_ended_covers_any_future_year():
    # Une plage "-présent" doit couvrir automatiquement une future
    # législature (ex. 2030) sans qu'aucun code ne change.
    assert mandats._year_in_ranges(2030, [(2024, None)]) is True
    assert mandats._year_in_ranges(2100, [(2024, None)]) is True


def test_year_in_ranges_closed_range_excludes_before_and_after():
    assert mandats._year_in_ranges(2017, [(2018, 2024)]) is False
    assert mandats._year_in_ranges(2020, [(2018, 2024)]) is True
    assert mandats._year_in_ranges(2025, [(2018, 2024)]) is False


# ── role_at : précédence Collège, repli None, dates absentes/invalides ─────
def test_role_at_unknown_person_returns_none(monkeypatch):
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {})
    assert mandats.role_at("inconnu", "2020-01-01") is None


def test_role_at_missing_or_empty_date_returns_none(monkeypatch):
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {
        "x": {"conseiller": [(2000, None)], "echevin": [], "bourgmestre": []},
    })
    assert mandats.role_at("x", None) is None
    assert mandats.role_at("x", "") is None


def test_role_at_college_takes_precedence(monkeypatch):
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {
        "x": {"conseiller": [(2000, None)], "echevin": [(2020, None)], "bourgmestre": []},
    })
    # Échevin·e ET conseiller·ère en même temps (cas normal, les échevin·e·s
    # restent conseiller·ère·s) : le Collège l'emporte pour le classement.
    assert mandats.role_at("x", "2021-01-01") == "college"
    # Avant le mandat d'échevin : simple conseiller·ère.
    assert mandats.role_at("x", "2010-01-01") == "conseiller"
    # Avant même le mandat de conseiller·ère : rôle inconnu à cette date.
    assert mandats.role_at("x", "1995-01-01") is None


def test_role_at_bourgmestre_also_counts_as_college(monkeypatch):
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {
        "x": {"conseiller": [(2000, None)], "echevin": [], "bourgmestre": [(2019, 2024)]},
    })
    assert mandats.role_at("x", "2020-01-01") == "college"
    assert mandats.role_at("x", "2025-01-01") == "conseiller"


# ── mandats_for : structure exposée pour l'affichage détaillé ──────────────
def test_mandats_for_returns_none_when_person_absent():
    assert mandats.mandats_for("inconnu") is None


def test_mandats_for_returns_none_when_all_ranges_empty(monkeypatch):
    # Ex. personne listée mais jamais élue (statut "Non élu / Candidat").
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {
        "x": {"conseiller": [], "echevin": [], "bourgmestre": []},
    })
    assert mandats.mandats_for("x") is None


def test_mandats_for_shapes_ranges_as_debut_fin_dicts(monkeypatch):
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {
        "x": {"conseiller": [(2012, 2019), (2024, None)], "echevin": [(2025, None)], "bourgmestre": []},
    })
    assert mandats.mandats_for("x") == {
        "conseiller": [{"debut": 2012, "fin": 2019}, {"debut": 2024, "fin": None}],
        "echevin": [{"debut": 2025, "fin": None}],
    }
