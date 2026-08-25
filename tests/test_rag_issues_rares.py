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


def test_les_points_repeches_passent_en_tete():
    """Porter l'issue demandée est une correspondance EXACTE ; le score n'est
    qu'une ressemblance. Laissés au score, quatre des dix motions rejetées
    seulement atteignaient les sources affichées (constaté en production)."""
    principaux = [_hit("PV-2020-06-24_SP52", 0.90), _hit("PV-2019-02-27_SP33", 0.80)]
    index = _IndexFactice([_hit("PV-2020-10-28_SP139", 0.55)])

    res = rag._renfort_issue(index, "motions rejetées", {}, "REJETÉ", principaux)

    assert [h["_id"] for h in res] == [
        "PV-2020-10-28_SP139", "PV-2020-06-24_SP52", "PV-2019-02-27_SP33",
    ]


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


# ── Le relevé exhaustif, tiré de la base et non de la recherche ────────────
def test_l_inventaire_compte_sur_la_base_pas_sur_les_extraits(monkeypatch):
    """Ce qui manquait pour que la réponse soit COMPLÈTE : la recherche avait
    ramené 4 des 10 motions rejetées, et la réponse annonçait « voici la liste »."""
    monkeypatch.setattr(rag, "points_par_issue",
                        lambda mot, limite: ([
                            {"date": "2024-05-29", "sp": 62, "titre": "Tronçon sud",
                             "vote": {"pour": 15, "contre": 19, "abstentions": 2}},
                        ], 10))
    bloc = rag._inventaire_block("REJETÉ")
    assert "10 point(s)" in bloc and "« Rejeté »" in bloc
    assert "29/05/2024 SP 62" in bloc                      # date à la française
    assert "(15 pour, 19 contre, 2 abstentions)" in bloc


def test_l_inventaire_dit_quand_il_est_tronque(monkeypatch):
    """645 reports ne tiennent pas dans un prompt : la liste est coupée, et le
    total réel est donné pour qu'une réponse ne la présente pas comme complète."""
    monkeypatch.setattr(rag, "points_par_issue",
                        lambda mot, limite: ([{"date": "2026-05-27", "sp": 53,
                                               "titre": "Moratoire", "vote": {}}], 645))
    bloc = rag._inventaire_block("REPORTÉ")
    assert "645 point(s)" in bloc and "plus récents" in bloc


def test_une_abstention_au_singulier(monkeypatch):
    monkeypatch.setattr(rag, "points_par_issue",
                        lambda mot, limite: ([{"date": "2014-09-24", "sp": 88, "titre": "Reyers",
                                               "vote": {"pour": 7, "contre": 34,
                                                        "abstentions": 1}}], 1))
    assert "1 abstention)" in rag._inventaire_block("REJETÉ")


def test_pas_d_inventaire_sans_issue_detectee():
    assert rag._inventaire_block(None) == ""
