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
