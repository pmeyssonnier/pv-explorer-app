"""Tests de `_is_excluded_amount` : distingue une VRAIE dépense discrétionnaire
de la commune d'un montant à écarter (budget global, transfert/dotation,
litige, opération d'intercommunale, acte non dépensier). Partagé par /stats et
/trend — s'il se trompe, les montants agrégés deviennent trompeurs.
"""
import pytest

from services.statistics import _is_excluded_amount


def _point(**kw):
    """Fabrique un point minimal ; surcharge les champs voulus."""
    base = {"type": "point_normal", "titre": "", "decision": "", "montant_eur": 10000}
    base.update(kw)
    return base


# ── À GARDER : dépenses réelles ─────────────────────────────────────────────
@pytest.mark.parametrize("titre", [
    "Marché public de fournitures de mobilier scolaire",
    "Subside à l'ASBL Maison de quartier",
    "Rénovation de la toiture de l'école communale",
])
def test_depense_reelle_gardee(titre):
    assert _is_excluded_amount(_point(titre=titre)) is False


# ── À EXCLURE : budgets globaux et comptes ──────────────────────────────────
@pytest.mark.parametrize("titre", [
    "Budget ordinaire de l'exercice 2020",
    "Modification budgétaire n°2",
    "Comptes annuels 2019",
    "Douzièmes provisoires",
])
def test_budget_global_exclu(titre):
    assert _is_excluded_amount(_point(titre=titre)) is True


# ── À EXCLURE : transferts / dotations ──────────────────────────────────────
def test_dotation_exclue():
    assert _is_excluded_amount(_point(titre="Dotation communale à la zone de police")) is True


# ── À EXCLURE : intercommunales (valeur d'actifs, pas une dépense) ──────────
@pytest.mark.parametrize("titre", [
    "Eandis — augmentation de capital",
    "Sibelga — modification des statuts",
])
def test_intercommunale_exclue(titre):
    assert _is_excluded_amount(_point(titre=titre)) is True


# ── À EXCLURE : types de points non dépensiers ──────────────────────────────
@pytest.mark.parametrize("type_", ["motion", "question_orale", "demande_habitant"])
def test_type_non_depensier_exclu(type_):
    assert _is_excluded_amount(_point(type=type_, titre="Sujet quelconque")) is True


# ── À EXCLURE : décision « prend pour information / prend acte » ─────────────
@pytest.mark.parametrize("decision", [
    "PREND POUR INFORMATION",
    "Le Conseil prend acte",
    "Prend connaissance du rapport",
])
def test_decision_non_depensiere_exclue(decision):
    assert _is_excluded_amount(_point(decision=decision, titre="Rapport annuel")) is True
