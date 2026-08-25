"""Séparation des trois dimensions d'un point : type / traitement / décision.

Ce que ces tests protègent : « REPORTÉ », « RETIRÉ » et « DÉBAT » ne sont pas
des décisions. Les ranger comme telles faisait répondre « la séance a été
reportée » à la question « qu'a décidé le conseil ? ». Le classement doit
rester DÉTERMINISTE (aucun PDF relu, aucun LLM) et IDEMPOTENT (un backfill
relancé ne doit rien changer).
"""
from split_statut_decision import split_statut_decision
from utils_statut import (
    STATUT_REPORTE, STATUT_RETIRE, STATUT_TRAITE, STATUTS_TRAITEMENT,
    classer_decision,
)


# ── classer_decision : la table des trois cases ────────────────────────────
def test_report_et_retrait_sont_des_statuts_pas_des_decisions():
    # Deux statuts DISTINCTS : reporté revient, retiré ne revient pas.
    assert classer_decision("REPORTÉ") == (STATUT_REPORTE, None, False)
    assert classer_decision("RETIRÉ") == (STATUT_RETIRE, None, False)


def test_debat_est_un_deroulement_pas_une_issue():
    assert classer_decision("DÉBAT") == (STATUT_TRAITE, None, True)


def test_vraie_decision_rendue_intacte():
    # Le stockage ne canonise pas : « PRENDS POUR INFORMATION » reste tel quel,
    # l'orthographe d'affichage est l'affaire de utils.text._DECISION_LABELS.
    for d in ("APPROUVÉ", "DÉCIDE", "PREND ACTE", "PRENDS POUR INFORMATION",
              "MINUTE DE SILENCE"):
        assert classer_decision(d) == (STATUT_TRAITE, d, False)


def test_decision_vide_reste_vide_et_traitee():
    # Régime NORMAL d'une question orale ou d'une demande d'habitant·e : ne
    # rien décider n'est pas une anomalie de traitement.
    assert classer_decision("") == (STATUT_TRAITE, "", False)
    assert classer_decision(None) == (STATUT_TRAITE, None, False)


def test_mention_de_vote_recollee_ne_masque_pas_le_statut():
    # « REPORTÉ à l'unanimité » reste un report, pas une décision inconnue.
    assert classer_decision("REPORTÉ à l'unanimité")[0] == STATUT_REPORTE
    assert classer_decision("Reporté (33 pour, 0 contre)")[0] == STATUT_REPORTE
    assert classer_decision("DÉBAT")[2] is True


def test_accents_et_casse_indifferents():
    assert classer_decision("reporte")[0] == STATUT_REPORTE
    assert classer_decision("Retire")[0] == STATUT_RETIRE
    assert classer_decision("debat")[2] is True


# ── split_statut_decision : le backfill dérivé ─────────────────────────────
def _base():
    return {"seances": [{
        "seance": {"date": "2016-10-26"},
        "points": [
            {"sp": 7, "type": "point_normal", "decision": "RETIRÉ"},
            {"sp": 8, "type": "motion", "decision": "REPORTÉ"},
            {"sp": 9, "type": "question_orale", "decision": "DÉBAT"},
            {"sp": 10, "type": "point_normal", "decision": "APPROUVÉ"},
            {"sp": 11, "type": "demande_habitant", "decision": ""},
        ],
    }]}


def test_split_range_chaque_point_dans_les_trois_dimensions():
    db = _base()
    changes = split_statut_decision(db)
    pts = {p["sp"]: p for p in db["seances"][0]["points"]}

    assert pts[7]["statut_traitement"] == STATUT_RETIRE and pts[7]["decision"] is None
    assert pts[8]["statut_traitement"] == STATUT_REPORTE and pts[8]["decision"] is None
    assert pts[9]["debat"] is True and pts[9]["decision"] is None
    # Une vraie décision n'est jamais effacée ni déplacée.
    assert pts[10]["statut_traitement"] == STATUT_TRAITE
    assert pts[10]["decision"] == "APPROUVÉ" and pts[10]["debat"] is False
    assert pts[11]["statut_traitement"] == STATUT_TRAITE and pts[11]["decision"] == ""

    # Seuls les points RÉELLEMENT déplacés sont rapportés.
    assert {c["sp"] for c in changes} == {7, 8, 9}
    assert all(p["statut_traitement"] in STATUTS_TRAITEMENT
               for p in db["seances"][0]["points"])


def test_split_est_idempotent():
    db = _base()
    split_statut_decision(db)
    avant = [dict(p) for p in db["seances"][0]["points"]]
    assert split_statut_decision(db) == []          # deuxième passe : rien
    assert db["seances"][0]["points"] == avant      # et rien n'a bougé


def test_split_ne_retouche_pas_un_point_deja_separe():
    # Un point séparé à la main (ou par une passe antérieure) fait foi : le
    # backfill ne rejuge jamais un statut déjà posé.
    db = {"seances": [{"seance": {"date": "2020-01-29"}, "points": [
        {"sp": 1, "type": "point_normal", "statut_traitement": STATUT_RETIRE,
         "debat": False, "decision": None},
    ]}]}
    assert split_statut_decision(db) == []
    assert db["seances"][0]["points"][0]["statut_traitement"] == STATUT_RETIRE
