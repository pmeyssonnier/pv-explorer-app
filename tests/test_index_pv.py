"""Tests des transformations d'indexation (`index_pv.py`) : mise en forme du
vote et construction d'un chunk (texte vectorisé + métadonnées + ID stable).
"""
import pytest

from index_pv import format_vote, point_to_chunk


# ── format_vote ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("vote, attendu", [
    ({"type": "unanimite"}, "approuvé à l'unanimité"),
    ({"type": "reporte"}, "point reporté"),
    ({"type": "vote_nominal", "pour": 25, "contre": 3, "abstentions": 2},
     "25 pour, 3 contre, 2 abstentions"),
    ({}, "vote non précisé"),
    (None, "vote non précisé"),
])
def test_format_vote(vote, attendu):
    assert format_vote(vote) == attendu


# ── point_to_chunk ──────────────────────────────────────────────────────────
def _seance():
    return {"id": "PV-2020-01-29", "date": "2020-01-29"}


def _point():
    return {
        "sp": 12, "rubrique": "Enseignement", "sous_rubrique": "Écoles",
        "titre": "Rénovation de l'école 1", "resume": "Travaux de toiture.",
        "decision": "APPROUVÉ", "vote": {"type": "unanimite"},
        "montant_eur": 65000, "intervenants": ["M. Dupont"], "repondant": "Mme Martin",
        "thematiques": ["education", "travaux"], "type": "point_normal",
    }


def test_chunk_id_stable():
    """L'ID doit être déterministe (upsert idempotent à la réindexation)."""
    c1 = point_to_chunk(_point(), _seance(), "schaerbeek")
    c2 = point_to_chunk(_point(), _seance(), "schaerbeek")
    assert c1["id"] == c2["id"] == "PV-2020-01-29_SP12"


def test_chunk_metadata_essentielle():
    c = point_to_chunk(_point(), _seance(), "schaerbeek")
    md = c["metadata"]
    assert md["commune"] == "schaerbeek"
    assert md["year"] == 2020                     # champ de filtrage temporel
    assert md["date"] == "2020-01-29"
    assert md["montant_eur"] == 65000.0
    assert md["thematiques"] == ["education", "travaux"]


def test_chunk_text_contient_le_contexte():
    c = point_to_chunk(_point(), _seance(), "schaerbeek")
    txt = c["metadata"]["chunk_text"]
    assert "Schaerbeek" in txt
    assert "2020-01-29" in txt
    assert "Rénovation de l'école 1" in txt
    assert "65 000" in txt                         # montant formaté espaces


def test_chunk_text_priorise_le_titre_avant_le_gabarit_generique():
    """Le titre (élément le plus distinctif, ex. le nom d'une ASBL) doit
    précéder les champs très répétitifs d'un point à l'autre (rubrique,
    décision, thématiques) : sinon le gabarit administratif générique domine
    le texte vectorisé et rapproche à tort des points sans rapport (ex. deux
    ASBL différentes ayant chacune leurs comptes « pris acte »)."""
    c = point_to_chunk(_point(), _seance(), "schaerbeek")
    txt = c["metadata"]["chunk_text"]
    assert txt.index("Rénovation de l'école 1") < txt.index("Rubrique")
    assert txt.index("Rénovation de l'école 1") < txt.index("Décision")
    assert txt.index("Rénovation de l'école 1") < txt.index("Thématiques")


def test_chunk_year_zero_si_date_absente():
    c = point_to_chunk(_point(), {"id": "PV-X", "date": "date inconnue"}, "schaerbeek")
    assert c["metadata"]["year"] == 0
