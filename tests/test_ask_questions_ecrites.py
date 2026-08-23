"""Test de services.rag.answer() pour une source question écrite
(source_type="question_ecrite") : pas de repli sur le PDF du PV par date, pas
de lien vidéo de séance, decision passée telle quelle (précalculée à
l'indexation — voir index_qe.question_to_chunk), comme pour un débat vidéo.
Pinecone et Anthropic monkeypatchés — même approche que test_ask_thematiques.py.
"""
from unittest.mock import MagicMock

import services.rag as rag


def _fake_text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _fake_client(text):
    client = MagicMock()
    client.messages.create.return_value = MagicMock(content=[_fake_text_block(text)])
    return client


def test_ask_question_ecrite_source_shape(monkeypatch):
    fake_index = MagicMock()
    fake_index.search.return_value = {
        "result": {
            "hits": [
                {
                    "_id": "QE-2025-015",
                    "score": 0.88,
                    "fields": {
                        "source_type": "question_ecrite",
                        "date": "2025-11-10",
                        "sp": 0,
                        "titre": "Le prix des garderies scolaires",
                        "decision": "Question de Georges Verzin",
                        "url": "https://1030.be/qe/015.pdf",
                        "reponse": "Le tarif est fixé par le règlement communal.",
                    },
                },
            ],
        },
    }
    monkeypatch.setattr(rag, "get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(rag, "get_anthropic", lambda: _fake_client("Réponse citant la question du 10/11/2025."))

    result = rag.answer("Quel est le prix des garderies scolaires ?", None)

    assert len(result.sources) == 1
    s = result.sources[0]
    assert s.source_type == "question_ecrite"
    assert s.url == "https://1030.be/qe/015.pdf"
    assert s.video_url is None
    assert s.decision == "Question de Georges Verzin"
    assert s.thematiques == []
    assert s.sp == 0
    assert s.reponse == "Le tarif est fixé par le règlement communal."


def test_ask_question_ecrite_reponse_none_when_not_yet_answered(monkeypatch):
    # Métadonnée "reponse" vide (chaîne vide, voir index_qe.question_to_chunk)
    # → None côté API, jamais une chaîne vide affichée comme un accordéon
    # ouvrable sans contenu.
    fake_index = MagicMock()
    fake_index.search.return_value = {
        "result": {
            "hits": [
                {
                    "_id": "QE-2026-008",
                    "score": 0.6,
                    "fields": {
                        "source_type": "question_ecrite",
                        "date": "2026-06-18",
                        "sp": 0,
                        "titre": "Test sans réponse",
                        "decision": "Question de Quentin Van den Hove",
                        "reponse": "",
                    },
                },
            ],
        },
    }
    monkeypatch.setattr(rag, "get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(rag, "get_anthropic", lambda: _fake_client("Réponse."))

    result = rag.answer("Question sans réponse ?", None)

    assert result.sources[0].reponse is None


def test_ask_pv_source_never_carries_reponse_field(monkeypatch):
    # "reponse" est spécifique aux questions écrites — jamais peuplé pour une
    # délibération (PV), même si la métadonnée en portait une par erreur.
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
                    },
                },
            ],
        },
    }
    monkeypatch.setattr(rag, "get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(rag, "get_anthropic", lambda: _fake_client("Réponse citant la séance du 14/01/2026."))

    result = rag.answer("Quels travaux dans les crèches ?", None)

    assert result.sources[0].reponse is None


def test_ask_question_ecrite_never_falls_back_to_pv_pdf_by_date(monkeypatch):
    # Une question écrite n'est jamais liée à une séance/un PV, même si sa
    # date coïncide par hasard avec celle d'un PV publié — le lien ne doit
    # jamais être deviné depuis la date (contrairement à une délibération).
    fake_index = MagicMock()
    fake_index.search.return_value = {
        "result": {
            "hits": [
                {
                    "_id": "QE-2026-002",
                    "score": 0.7,
                    "fields": {
                        "source_type": "question_ecrite",
                        "date": "2026-04-22",   # même date qu'une vraie séance du corpus
                        "sp": 0,
                        "titre": "Travaux de la Task Force Quartier Nord",
                        "decision": "Question de Bernard Clerfayt",
                        "url": "",
                    },
                },
            ],
        },
    }
    monkeypatch.setattr(rag, "get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(rag, "get_anthropic", lambda: _fake_client("Réponse."))

    result = rag.answer("Où en est la Task Force Quartier Nord ?", None)

    assert result.sources[0].url is None
    assert result.sources[0].video_url is None


def test_ask_question_ecrite_repondant_appears_in_decision_when_known(monkeypatch):
    fake_index = MagicMock()
    fake_index.search.return_value = {
        "result": {
            "hits": [
                {
                    "_id": "QE-2025-020",
                    "score": 0.75,
                    "fields": {
                        "source_type": "question_ecrite",
                        "date": "2025-12-01",
                        "sp": 0,
                        "titre": "Test",
                        "decision": "Question de Georges Verzin · répondu par Bernard Clerfayt",
                    },
                },
            ],
        },
    }
    monkeypatch.setattr(rag, "get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(rag, "get_anthropic", lambda: _fake_client("Réponse."))

    result = rag.answer("Question test ?", None)

    assert result.sources[0].decision == "Question de Georges Verzin · répondu par Bernard Clerfayt"
