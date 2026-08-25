"""Repêchage des issues RARES dans la recherche (`services/rag._renfort_issue`).

Constaté en production : à la question « Le conseil a-t-il déjà rejeté des
motions ? », l'app répondait qu'elle n'en trouvait aucune — alors que les dix
motions rejetées étaient bien indexées. La recherche vectorielle ramène les 30
passages les plus proches parmi 10 062 ; dans un point rejeté, le mot
« REJETÉ » occupe une ligne d'un texte dominé par le titre et le résumé. Trente
places, dix cibles, aucun filtre : les dix ne remontaient pas.

Le champ `decision` étant une métadonnée Pinecone, une seconde recherche
filtrée va les chercher. Ces tests figent les deux propriétés qui rendent ce
repêchage sûr : il AJOUTE (il ne détourne jamais la recherche principale), et
il ne fait jamais échouer une réponse.
"""
import pytest

from services import rag
from utils.statut import decision_recherchee


# ── Quand faut-il repêcher ? ───────────────────────────────────────────────
@pytest.mark.parametrize("question,attendu", [
    ("Le conseil a-t-il déjà rejeté des motions ?", "REJETÉ"),
    ("Quelles motions ont été rejetées en 2022 ?", "REJETÉ"),
    ("Quels points ont été reportés en 2020 ?", "REPORTÉ"),
    ("Combien de points ajournés cette année ?", "REPORTÉ"),
    ("Quels points ont été retirés de l'ordre du jour ?", "RETIRÉ"),
    ("Y a-t-il eu un retrait de point en 2016 ?", "RETIRÉ"),
])
def test_une_question_sur_une_issue_rare_declenche_le_repechage(question, attendu):
    assert decision_recherchee(question) == attendu


@pytest.mark.parametrize("question", [
    "Quel est le budget de la propreté publique ?",
    "Le rejet des eaux usées dans le Maelbeek",     # substantif : un SUJET de séance
    "Qui a approuvé le budget 2024 ?",              # issue courante : le top 30 en contient
])
def test_une_question_ordinaire_ne_declenche_rien(question):
    """Un repêchage à tort ne coûterait que quelques passages, mais le
    substantif « rejet » désigne le plus souvent un sujet, pas le sort d'un
    point : ne pas le confondre évite d'aller chercher des motions quand on
    parle d'assainissement."""
    assert decision_recherchee(question) is None


# ── Ce que le repêchage fait, et ne fait pas ───────────────────────────────
class _IndexFactice:
    """Index Pinecone minimal : renvoie des hits fixes et retient la requête."""

    def __init__(self, hits, erreur=None):
        self.hits, self.erreur, self.requetes = hits, erreur, []

    def search(self, namespace=None, query=None, timeout=None):
        self.requetes.append(query)
        if self.erreur:
            raise self.erreur
        return {"result": {"hits": self.hits}}


def _hit(id_, score):
    return {"_id": id_, "score": score}


def test_le_repechage_ajoute_les_points_manquants_et_garde_l_ordre_des_scores():
    principaux = [_hit("PV-2020-06-24_SP52", 0.90), _hit("PV-2019-02-27_SP33", 0.80)]
    index = _IndexFactice([_hit("PV-2020-10-28_SP139", 0.85)])

    res = rag._renfort_issue(index, "motions rejetées", {}, "REJETÉ", principaux)

    assert [h["_id"] for h in res] == [
        "PV-2020-06-24_SP52", "PV-2020-10-28_SP139", "PV-2019-02-27_SP33",
    ]
    # Le classement reste celui des scores : on ne truque pas la pertinence.
    assert [h["score"] for h in res] == [0.90, 0.85, 0.80]


def test_le_repechage_filtre_sur_la_decision_sans_perdre_les_filtres_existants():
    index = _IndexFactice([])
    rag._renfort_issue(index, "motions rejetées", {"year": {"$eq": 2022}}, "REJETÉ", [])
    filtre = index.requetes[0]["filter"]
    # L'année demandée n'est pas écrasée par le filtre d'issue : une question
    # sur 2022 ne doit pas se mettre à citer 2014.
    assert filtre == {"year": {"$eq": 2022}, "decision": {"$eq": "REJETÉ"}}


def test_un_point_deja_trouve_n_est_pas_dedouble():
    deja = [_hit("PV-2020-10-28_SP139", 0.91)]
    index = _IndexFactice([_hit("PV-2020-10-28_SP139", 0.88)])
    res = rag._renfort_issue(index, "motions rejetées", {}, "REJETÉ", deja)
    assert len(res) == 1 and res[0]["score"] == 0.91


def test_un_repechage_en_echec_ne_casse_jamais_la_reponse():
    """Quota épuisé, timeout : la réponse principale ne dépend pas de l'appoint."""
    principaux = [_hit("PV-2020-06-24_SP52", 0.90)]
    index = _IndexFactice([], erreur=RuntimeError("RESOURCE_EXHAUSTED"))
    assert rag._renfort_issue(index, "q", {}, "REJETÉ", principaux) == principaux
