"""Le conseil peut dire NON — et l'app doit le montrer.

Sur 222 motions, la base n'en comptait aucune comme rejetée alors que 10 ont
recueilli plus de contre que de pour. La cause n'était pas la lecture des PV
mais le VOCABULAIRE : la liste des décisions du schéma d'extraction n'avait pas
de mot pour « non ». Faute de case, l'extracteur rangeait une motion battue
sous « DÉBAT » — ou, deux fois, sous « DÉCIDE », si bien qu'une motion battue
par 19 voix contre 24 s'affichait « Décidé ».

Ces tests figent les trois pièces du correctif : le mot existe, il l'emporte
sur le débat qui l'a précédé, et il se dérive du vote sans relire un PDF.
"""
import copy

from backfill_rejets import DECISION_REJET, backfill_rejets
from pv_extraction_pipeline import _recover_decision_from_window
from services import seances
from utils_statut import STATUT_TRAITE, classer_decision, dimensions, mot_issue


def _motion(sp, pour, contre, decision=None, debat=True, type_="motion", abst=0):
    return {"sp": sp, "type": type_, "titre": f"Motion {sp}", "resume": "",
            "decision": decision, "statut_traitement": STATUT_TRAITE, "debat": debat,
            "vote": {"type": "vote_nominal", "pour": pour, "contre": contre,
                     "abstentions": abst},
            "thematiques": [], "auteurs": [], "intervenants": [], "repondants": []}


def _db(points, date="1999-01-07"):
    return {"seances": [{"seance": {"date": date, "source_url": "http://pv"},
                         "points": points}]}


# ── Le mot existe, et c'est une DÉCISION ───────────────────────────────────
def test_un_rejet_est_une_decision_pas_un_statut_de_traitement():
    # Le conseil s'est prononcé : le point a bien été traité, il a dit non.
    assert classer_decision("REJETÉ") == (STATUT_TRAITE, "REJETÉ", False)


def test_une_motion_debattue_puis_rejetee_a_pour_issue_le_rejet():
    """Le débat est ce qui s'est passé, le rejet est ce qui a été décidé."""
    p = _motion(1, 16, 22, decision="REJETÉ", debat=True)
    statut, decision, debat = dimensions(p)
    assert (statut, decision, debat) == (STATUT_TRAITE, "REJETÉ", True)
    assert mot_issue(p) == "REJETÉ"          # et non « DÉBAT »


def test_un_point_debattu_sans_decision_reste_un_debat():
    assert mot_issue(_motion(1, None, None, decision=None, debat=True)) == "DÉBAT"


def test_l_app_affiche_le_rejet_et_son_decompte(monkeypatch):
    monkeypatch.setattr(seances, "load_db",
                        lambda: _db([_motion(1, 16, 22, decision="REJETÉ", abst=1)]))
    p = seances.seance_detail("1999-01-07")["points"][0]
    assert p["statut"] == "Rejeté"                                   # la puce
    assert p["decision"] == "Rejeté (16 pour, 22 contre, 1 abstention)"   # la carte


# ── La dérivation depuis le vote déjà stocké ───────────────────────────────
def test_une_motion_battue_par_son_vote_devient_rejetee():
    db = _db([_motion(1, 16, 22)])
    bilan = backfill_rejets(db)
    p = db["seances"][0]["points"][0]
    assert p["decision"] == DECISION_REJET
    assert [f["sp"] for f in bilan["appliques"]] == [1]


def test_le_debat_n_est_pas_efface_par_le_rejet():
    """Les deux dimensions coexistent — tout l'intérêt de les avoir séparées."""
    db = _db([_motion(1, 16, 22, debat=True)])
    backfill_rejets(db)
    p = db["seances"][0]["points"][0]
    assert p["debat"] is True and p["statut_traitement"] == STATUT_TRAITE


def test_une_decision_contredite_par_son_propre_vote_est_corrigee():
    # 19 pour / 24 contre marqué « DÉCIDE » : entre le mot et le décompte,
    # c'est le décompte qui fait foi. Seul cas d'écrasement du dépôt.
    db = _db([_motion(139, 19, 24, decision="DÉCIDE", debat=False)])
    bilan = backfill_rejets(db)
    assert db["seances"][0]["points"][0]["decision"] == DECISION_REJET
    assert bilan["appliques"][0]["avant"] == "DÉCIDE"


def test_une_motion_adoptee_n_est_jamais_touchee():
    db = _db([_motion(1, 30, 7, decision="APPROUVÉ")])
    assert backfill_rejets(db)["appliques"] == []
    assert db["seances"][0]["points"][0]["decision"] == "APPROUVÉ"


def test_l_egalite_est_signalee_mais_pas_appliquee():
    """18-18 : la proposition n'est pas adoptée, mais seul le PV le dit
    explicitement — on ne l'écrit pas à sa place."""
    db = _db([_motion(59, 18, 18)])
    bilan = backfill_rejets(db)
    assert bilan["appliques"] == []
    assert [f["sp"] for f in bilan["signales"]] == [59]
    assert db["seances"][0]["points"][0]["decision"] is None


def test_un_point_non_motion_est_signale_jamais_corrige():
    """Sur un point délibératif, le décompte enregistré appartient parfois à un
    amendement ou à une motion d'ordre : l'arithmétique n'y prouve rien."""
    db = _db([_motion(18, 15, 21, decision="APPROUVÉ", type_="point_normal")])
    bilan = backfill_rejets(db)
    assert bilan["appliques"] == []
    assert bilan["signales"][0]["type"] == "point_normal"
    assert db["seances"][0]["points"][0]["decision"] == "APPROUVÉ"


def test_backfill_rejets_est_idempotent():
    db = _db([_motion(1, 16, 22), _motion(2, 30, 7, decision="APPROUVÉ")])
    backfill_rejets(db)
    avant = copy.deepcopy(db)
    assert backfill_rejets(db)["appliques"] == []
    assert db == avant


# ── L'extraction : le rejet écrit noir sur blanc ───────────────────────────
def test_la_recuperation_deterministe_enregistre_un_rejet_explicite():
    assert _recover_decision_from_window("La motion est rejetée par 22 non, 16 oui")[0] == "REJETÉ"
    assert _recover_decision_from_window("de motie werd verworpen")[0] == "REJETÉ"
    # Un rejet prime sur une formule d'agenda restée dans la même fenêtre.
    assert _recover_decision_from_window("Convention - Approbation. Point rejeté.")[0] == "REJETÉ"


def test_le_substantif_rejet_ne_declenche_rien():
    """« Rejet de la demande de réétude du tunnel » décrit ce que la motion
    DEMANDE, pas son sort : y lire un rejet du point inverserait son sens."""
    assert _recover_decision_from_window(
        "Rejet de la demande de réétude du tunnel voiture sous Meiser")[0] is None
    assert _recover_decision_from_window(
        "Le conseil décide de rejeter le projet régional")[0] is None
