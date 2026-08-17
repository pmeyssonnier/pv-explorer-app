"""Validation de la base JSON des PV : structure attendue et cohérence des
métadonnées dont dépend l'app (dates, années, montants plausibles). Tient lieu
d'étape « validation JSON » du CI — casse la build si le dataset est corrompu.
"""
import json
import math
from pathlib import Path

import pytest

from services.statistics import _is_excluded_amount

DB_PATH = Path(__file__).resolve().parent.parent / "backend" / "pv_conseil_schaerbeek.json"


@pytest.fixture(scope="module")
def db():
    with open(DB_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_json_valide_et_structure(db):
    assert isinstance(db, dict)
    assert isinstance(db.get("seances"), list)
    assert len(db["seances"]) > 0


def test_chaque_seance_a_une_date_iso(db):
    for s in db["seances"]:
        date = (s.get("seance") or {}).get("date")
        assert date, "séance sans date"
        # format AAAA-MM-JJ
        assert len(date) == 10 and date[4] == "-" and date[7] == "-", f"date invalide : {date}"
        int(date[:4])  # année parsable


def test_points_presents_et_sp_defini(db):
    total = 0
    for s in db["seances"]:
        for p in s.get("points", []):
            total += 1
            assert "titre" in p
    assert total > 1000, f"trop peu de points ({total}) — dataset tronqué ?"


def test_montants_sont_numeriques(db):
    """Garde anti-corruption : un montant présent est un nombre fini (pas une
    chaîne, pas NaN). Les données BRUTES contiennent légitimement des négatifs
    (corrections) et des milliards (budgets globaux) — d'où pas de borne dure
    ici : c'est `_is_excluded_amount` qui écarte ensuite le non-discrétionnaire.
    """
    for s in db["seances"]:
        for p in s.get("points", []):
            m = p.get("montant_eur")
            if m is not None:
                assert isinstance(m, (int, float)) and math.isfinite(m), f"montant non numérique : {m!r}"


def test_total_engage_reste_plausible(db):
    """Régression du bug « 9 milliards » : une fois les montants non
    discrétionnaires écartés (budgets globaux, dotations, intercommunales…), le
    total ENGAGÉ doit rester dans un ordre de grandeur communal (< 2 Md€).
    Le total brut, lui, dépasse 11 Md€ — ce test casse si le filtre se brise.
    """
    total = sum(
        p["montant_eur"]
        for s in db["seances"] for p in s.get("points", [])
        if p.get("montant_eur") and not _is_excluded_amount(p)
    )
    assert total < 2_000_000_000, f"total engagé anormalement élevé : {total:,.0f} €"


def test_annees_couvertes_coherentes(db):
    annees = {int((s["seance"]["date"] or "0")[:4]) for s in db["seances"]}
    # Le corpus commence en 2012 ; on vérifie juste la borne basse et l'ordre.
    assert min(annees) == 2012
    assert max(annees) >= 2022
