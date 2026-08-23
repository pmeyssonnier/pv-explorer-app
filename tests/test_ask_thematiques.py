"""Test de services.rag.answer() : les thématiques ET le statut de décision
du point (métadonnées Pinecone) doivent apparaître, normalisés, dans chaque
Source renvoyée par /ask — même normalisation d'affichage que Statistiques/
Séances/Par élu·e (voir utils.text._thematique_label/_decision_status).
Pinecone et Anthropic sont monkeypatchés (aucun appel réel), même approche
que test_pv_integration.py.
"""
from unittest.mock import MagicMock

import services.rag as rag


def _fake_text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def test_ask_sources_carry_normalized_thematiques(monkeypatch):
    fake_index = MagicMock()
    fake_index.search.return_value = {
        "result": {
            "hits": [
                {
                    "_id": "PV-2026-01-14-sp3",
                    "score": 0.9,
                    "fields": {
                        "source_type": "pv",
                        "date": "2026-01-14",
                        "sp": 3,
                        "titre": "Rénovation d'une crèche",
                        "decision": "DECIDE",
                        "vote_type": "unanimite",
                        "thematiques": ["transports_publics", "petite_enfance"],
                    },
                },
            ],
        },
    }
    monkeypatch.setattr(rag, "get_pinecone_index", lambda: fake_index)

    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[_fake_text_block("Réponse citant la séance du 14/01/2026, SP3.")]
    )
    monkeypatch.setattr(rag, "get_anthropic", lambda: fake_client)

    result = rag.answer("Quels travaux dans les crèches ?", None)

    assert len(result.sources) == 1
    # Même normalisation que _thematique_label ailleurs dans l'app (accents
    # retirés, underscores → espaces, casse capitalisée) — pas le slug brut.
    assert result.sources[0].thematiques == ["Transport public", "Petite enfance"]
    # Statut de décision normalisé (label + issue du vote), pas le texte
    # brut "DECIDE" indexé — même normalisation que Séances/Par élu·e.
    assert result.sources[0].decision == "Décidé à l'unanimité"


def test_ask_source_thematiques_defaults_to_empty_list(monkeypatch):
    # Un débat filmé (source_type=video_conseil) n'a pas de thématiques
    # indexées — la métadonnée est absente, pas juste vide.
    fake_index = MagicMock()
    fake_index.search.return_value = {
        "result": {
            "hits": [
                {
                    "_id": "video-abc123-42",
                    "score": 0.85,
                    "fields": {
                        "source_type": "video_conseil",
                        "date": "2026-01-14",
                        "sp": 0,
                        "titre": "Débat sur la mobilité",
                        "decision": "Débat · Georges Verzin",
                    },
                },
            ],
        },
    }
    monkeypatch.setattr(rag, "get_pinecone_index", lambda: fake_index)

    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[_fake_text_block("Réponse citant le débat du 14/01/2026.")]
    )
    monkeypatch.setattr(rag, "get_anthropic", lambda: fake_client)

    result = rag.answer("Qu'a dit le conseil sur la mobilité ?", None)

    assert len(result.sources) == 1
    assert result.sources[0].thematiques == []
    # Un débat filmé porte déjà "Type · Auteur·e" dans "decision" (ex.
    # "Débat · Georges Verzin") — pas une issue de vote : laissé tel quel,
    # jamais passé par _decision_status (réservé aux délibérations PV).
    assert result.sources[0].decision == "Débat · Georges Verzin"


def test_ask_source_decision_nominal_vote_has_no_fabricated_counts(monkeypatch):
    # Le vote nominal n'est indexé QUE par son type (pas le détail pour/
    # contre/abstentions, voir index_pv.py) — le statut affiché doit rester
    # honnête (label seul), jamais un chiffre inventé.
    fake_index = MagicMock()
    fake_index.search.return_value = {
        "result": {
            "hits": [
                {
                    "_id": "PV-2026-01-14-sp7",
                    "score": 0.8,
                    "fields": {
                        "source_type": "pv",
                        "date": "2026-01-14",
                        "sp": 7,
                        "titre": "Budget participatif 2026",
                        "decision": "DECIDE",
                        "vote_type": "vote_nominal",
                    },
                },
            ],
        },
    }
    monkeypatch.setattr(rag, "get_pinecone_index", lambda: fake_index)

    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[_fake_text_block("Réponse citant la séance du 14/01/2026, SP7.")]
    )
    monkeypatch.setattr(rag, "get_anthropic", lambda: fake_client)

    result = rag.answer("Quel budget participatif ?", None)

    assert result.sources[0].decision == "Décidé"
