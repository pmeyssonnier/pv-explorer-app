"""Tests de services.people.attribution._point_respondents et de son usage
dans services.seances/services.people.registry.

Jusqu'au 25/08/2026, pipeline/pv_extraction_pipeline.py écrivait un champ
`repondant` (texte, parfois une simple mention de rôle comme « Bourgmestre
ff », résolue via services.seances/registry en lisant les métadonnées de
séance). Depuis ce jour, normalize_point() n'écrit plus QUE `repondants`
(liste de noms déjà individualisés par Claude, plus aucun rôle à résoudre)
— mais services.seances et services.people.registry ne lisaient encore QUE
`repondant` (singulier). Conséquence, restée invisible tant qu'aucun PV
n'avait été publié avec le nouveau schéma (voir historique) : le prochain PV
publié depuis le panneau admin n'aurait affiché AUCUN répondant·e dans les
onglets Séances/Par élu·e, alors que le chat et Pinecone (qui lisent
`repondants` directement, sans passer par cette résolution) l'auraient
montré correctement.

_point_respondents() couvre les deux ères : `repondant` prioritaire s'il est
présent (résolution historique, y compris rôle seul type « Bourgmestre
ff » — comportement inchangé pour les ~10 000 points déjà publiés), sinon
`repondants` utilisé tel quel (nouveau schéma).
"""
import services.people.registry as registry
from services import seances
from services.people.attribution import _point_respondents


def _seance(points, date="1999-01-07", **meta):
    return {"seances": [{"seance": {"date": date, **meta}, "points": points}]}


# ── _point_respondents (fonction pure) ──────────────────────────────────────
def test_ancien_schema_repondant_simple():
    p = {"repondant": "Denis Grimberghs"}
    assert _point_respondents(p, {}) == ["Denis Grimberghs"]


def test_ancien_schema_role_seul_resolu_via_la_seance():
    p = {"repondant": "Bourgmestre ff"}
    assert _point_respondents(p, {"bourgmestre_ff": "Cécile Jodogne"}) == ["Cécile Jodogne"]


def test_nouveau_schema_repondant_absent_utilise_repondants():
    # normalize_point() actuel : plus de `repondant` du tout, seulement
    # `repondants` — déjà une liste de noms individualisés, rien à résoudre.
    p = {"repondants": ["Cécile Jodogne", "Frédéric Nimal"]}
    assert _point_respondents(p, {}) == ["Cécile Jodogne", "Frédéric Nimal"]


def test_nouveau_schema_repondants_vide_donne_liste_vide_pas_une_erreur():
    p = {"repondants": []}
    assert _point_respondents(p, {}) == []


def test_repondant_present_mais_vide_retombe_sur_repondants():
    # Chaîne vide ("" est falsy) : traité comme absent, pas comme "personne
    # ne répond" — bascule sur repondants si présent.
    p = {"repondant": "", "repondants": ["Cécile Jodogne"]}
    assert _point_respondents(p, {}) == ["Cécile Jodogne"]


# ── Intégration : l'onglet Séances retrouve le/la répondant·e d'un point du
# nouveau schéma (repondants seul, sans repondant) ──────────────────────────
def test_seance_detail_affiche_repondant_du_nouveau_schema(monkeypatch):
    monkeypatch.setattr(seances, "load_db", lambda: _seance([
        {"sp": 1, "type": "point_normal", "titre": "Objet", "resume": "",
         "decision": "APPROUVÉ", "vote": None, "thematiques": [],
         "auteurs": [], "intervenants": [], "repondants": ["Cécile Jodogne"]},
    ]))
    p = seances.seance_detail("1999-01-07")["points"][0]
    assert p["repondant"] == "Cécile Jodogne"


def test_seance_detail_repondant_absent_reste_none_pas_une_erreur(monkeypatch):
    monkeypatch.setattr(seances, "load_db", lambda: _seance([
        {"sp": 1, "type": "point_normal", "titre": "Objet", "resume": "",
         "decision": "APPROUVÉ", "vote": None, "thematiques": [],
         "auteurs": [], "intervenants": [], "repondants": []},
    ]))
    p = seances.seance_detail("1999-01-07")["points"][0]
    assert p["repondant"] is None


# ── Intégration : l'onglet Par élu·e (registre par personne) retrouve aussi
# le/la répondant·e d'un point du nouveau schéma ────────────────────────────
def test_registry_repond_list_du_nouveau_schema(monkeypatch):
    monkeypatch.setattr(registry, "load_db", lambda: _seance([
        {"sp": 1, "type": "question_orale", "titre": "Les nids-de-poule ?",
         "resume": "", "decision": "DÉBAT", "vote": None, "thematiques": [],
         "auteurs": ["Georges Verzin"], "intervenants": [],
         "repondants": ["Cécile Jodogne"]},
    ]))
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": []})
    index, pairs, nom_by_key = registry._build_all()
    repond = [it for it in index["jodogne"]["repond"] if it["date"] == "1999-01-07"]
    assert len(repond) == 1
    assert repond[0]["titre"] == "Les nids-de-poule ?"
