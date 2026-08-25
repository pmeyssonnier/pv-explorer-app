"""La séparation des dimensions ne doit RIEN changer à ce que l'app montre.

Le stockage passe d'un champ `decision` qui mélangeait trois questions à trois
champs distincts (`statut_traitement`, `debat`, `decision`). Les deux ne se
déploient pas ensemble — le code part avant la base, et un PV intégré par le
panneau admin peut encore arriver à l'ancien format. Ces tests figent
l'invariant qui rend la migration sûre :

    pour un même point, l'API répond la même chose avant et après séparation.

Sans lui, séparer la base afficherait 1 480 points « sans issue relevée » —
les 645 reports, 6 retraits et 829 débats dont le mot aurait quitté `decision`
sans que personne ne sache où le chercher.
"""
import copy

import pytest

from index_pv import point_to_chunk
from services import seances
from services.statistics import compute_stats
from utils_statut import (
    STATUT_REPORTE, STATUT_TRAITE, classer_decision, decision_manquante,
    dimensions, mot_issue, poser_decision,
)

# Les cinq issues du corpus, dans leur graphie d'origine : une vraie décision,
# les deux statuts de traitement, le débat, et l'absence d'issue (le régime
# NORMAL d'une question orale).
_ISSUES = ["APPROUVÉ", "PREND POUR INFORMATION", "REPORTÉ", "RETIRÉ", "DÉBAT", ""]


def _point(decision, sp=1, type_="point_normal"):
    return {"sp": sp, "type": type_, "titre": f"Point {sp}", "resume": "",
            "decision": decision, "vote": None, "thematiques": [],
            "auteurs": [], "intervenants": [], "repondants": []}


def _separe(point):
    """Le même point, tel que split_statut_decision l'écrit."""
    p = copy.deepcopy(point)
    p["statut_traitement"], p["decision"], p["debat"] = classer_decision(p.get("decision"))
    return p


def _seance(points, date="1999-01-07"):
    return {"seances": [{"seance": {"date": date, "source_url": "http://pv"},
                         "points": points}]}


# ── L'invariant : l'API ne bouge pas ───────────────────────────────────────
@pytest.mark.parametrize("decision", _ISSUES)
def test_le_point_rendu_par_l_api_est_identique_avant_et_apres(monkeypatch, decision):
    avant = _point(decision)
    apres = _separe(avant)

    def detail(points):
        monkeypatch.setattr(seances, "load_db", lambda: _seance(points))
        return seances.seance_detail("1999-01-07")["points"]

    assert detail([avant]) == detail([apres])


def test_un_report_reste_un_report_apres_separation(monkeypatch):
    """Le cas qui casserait en silence : le mot a quitté `decision`, et
    pourtant la puce, le badge et le drapeau doivent rester les mêmes."""
    monkeypatch.setattr(seances, "load_db", lambda: _seance([_separe(_point("REPORTÉ"))]))
    p = seances.seance_detail("1999-01-07")["points"][0]
    assert p["statut"] == "Reporté" and p["reporte"] is True and p["retire"] is False
    assert p["decision"] == "Reporté"


def test_un_debat_reste_une_issue_et_non_un_trou(monkeypatch):
    monkeypatch.setattr(seances, "load_db", lambda: _seance([_separe(_point("DÉBAT"))]))
    p = seances.seance_detail("1999-01-07")["points"][0]
    # « Sans issue relevée » se lit `statut` vide : un débat n'en est pas un.
    assert p["statut"] == "Débat" and p["reporte"] is False


# ── Les autres lectures : mêmes chiffres, même texte vectorisé ─────────────
@pytest.mark.parametrize("decision", _ISSUES)
def test_le_texte_vectorise_ne_bouge_pas(decision):
    """Corollaire concret : la séparation n'oblige à ré-embedder AUCUN vecteur
    (le quota d'embedding Pinecone est limité)."""
    seance = {"id": "PV1999", "date": "1999-01-07"}
    avant = point_to_chunk(_point(decision), seance, "schaerbeek")
    apres = point_to_chunk(_separe(_point(decision)), seance, "schaerbeek")
    assert avant["metadata"]["chunk_text"] == apres["metadata"]["chunk_text"]
    assert avant["metadata"]["decision"] == apres["metadata"]["decision"]


def test_le_decompte_des_decisions_de_stats_ne_bouge_pas():
    points = [_point(d, sp=i) for i, d in enumerate(_ISSUES, start=1)]
    avant = compute_stats(_seance(points))["decisions"]
    apres = compute_stats(_seance([_separe(p) for p in points]))["decisions"]
    assert sorted(map(str, dict(avant))) == sorted(map(str, dict(apres)))
    assert dict(avant)["REPORTÉ"] == 1 and dict(apres)["REPORTÉ"] == 1


def test_un_point_reporte_n_attend_aucune_reponse_orale():
    """attribution._sans_reponse_attendue lit le statut, plus le mot : un point
    reporté n'a pas été débattu, aucun·e répondant·e n'y manque."""
    from services.people.attribution import _sans_reponse_attendue
    assert _sans_reponse_attendue(_point("REPORTÉ", type_="question_orale")) is True
    assert _sans_reponse_attendue(_separe(_point("REPORTÉ", type_="question_orale"))) is True
    assert _sans_reponse_attendue(_separe(_point("APPROUVÉ"))) is False


# ── Les audits : un report n'est pas un trou d'extraction ──────────────────
def test_un_report_separe_n_est_pas_une_decision_manquante():
    assert decision_manquante(_point("")) is True
    assert decision_manquante(_point("REPORTÉ")) is False
    assert decision_manquante(_separe(_point("REPORTÉ"))) is False   # decision vidée
    assert decision_manquante(_separe(_point("DÉBAT"))) is False
    assert decision_manquante(_separe(_point("APPROUVÉ"))) is False


def test_audit_des_decisions_ignore_les_points_separes():
    from audit_completeness import audit_decisions
    db = _seance([_separe(_point("REPORTÉ", sp=1)), _separe(_point("DÉBAT", sp=2)),
                  _separe(_point("", sp=3))])
    rapport = audit_decisions(db)
    # Seul le SP 3 manque vraiment une décision.
    assert [r["sp"] for r in rapport] == [[3]]


# ── L'écriture : dans la forme du point, jamais l'autre ────────────────────
def test_poser_decision_ecrit_dans_la_forme_du_point():
    ancien = _point("")
    poser_decision(ancien, "RETIRÉ")
    # Base d'origine : le mot va dans `decision`, seul champ existant.
    assert ancien["decision"] == "RETIRÉ" and "statut_traitement" not in ancien

    nouveau = _separe(_point(""))
    poser_decision(nouveau, "RETIRÉ")
    # Base séparée : le retrait est un STATUT, la décision reste vide.
    assert nouveau["statut_traitement"] == "retiré" and nouveau["decision"] is None


def test_les_dimensions_se_lisent_pareil_dans_les_deux_formes():
    for d in _ISSUES:
        assert dimensions(_point(d)) == dimensions(_separe(_point(d)))
        assert mot_issue(_point(d)) == mot_issue(_separe(_point(d)))
    assert dimensions(_point("REPORTÉ"))[0] == STATUT_REPORTE
    assert dimensions(_point("APPROUVÉ"))[0] == STATUT_TRAITE
