"""Tests de backend/index_qe.py — question_to_chunk (fonction pure, aucun
appel réseau). Mêmes conventions que index_pv.point_to_chunk : id stable,
métadonnées de filtrage (commune/year/source_type), chunk_text vectorisé.
"""
import index_qe


def _q(**overrides):
    data = {
        "id": "QE-2025-015", "annee": 2025, "numero": 15, "date": "2025-11-10",
        "auteur": "Georges Verzin", "titre": "Le prix des garderies scolaires",
        "question": "Quel est le tarif appliqué ?",
        "reponse": "Le tarif est fixé par le règlement communal.",
        "repondant": "Bernard Clerfayt",
    }
    data.update(overrides)
    return data


def test_question_to_chunk_id_is_the_question_id():
    chunk = index_qe.question_to_chunk(_q(), "schaerbeek")
    assert chunk["id"] == "QE-2025-015"


def test_question_to_chunk_metadata_shape():
    chunk = index_qe.question_to_chunk(_q(), "schaerbeek")
    meta = chunk["metadata"]
    assert meta["source_type"] == "question_ecrite"
    assert meta["commune"] == "schaerbeek"
    assert meta["date"] == "2025-11-10"
    assert meta["year"] == 2025
    assert meta["sp"] == 0
    assert meta["titre"] == "Le prix des garderies scolaires"
    assert meta["thematiques"] == []


def test_question_to_chunk_decision_combines_auteur_and_repondant():
    chunk = index_qe.question_to_chunk(_q(), "schaerbeek")
    assert chunk["metadata"]["decision"] == "Question de Georges Verzin · répondu par Bernard Clerfayt"


def test_question_to_chunk_decision_without_repondant():
    chunk = index_qe.question_to_chunk(_q(repondant=None), "schaerbeek")
    assert chunk["metadata"]["decision"] == "Question de Georges Verzin"


def test_question_to_chunk_text_includes_question_and_reponse():
    chunk = index_qe.question_to_chunk(_q(), "schaerbeek")
    text = chunk["metadata"]["chunk_text"]
    assert "Quel est le tarif appliqué ?" in text
    assert "Le tarif est fixé par le règlement communal." in text
    assert "Georges Verzin" in text


def test_question_to_chunk_reponse_absente_ne_devine_rien():
    chunk = index_qe.question_to_chunk(_q(reponse=None), "schaerbeek")
    assert "Pas encore de réponse publiée." in chunk["metadata"]["chunk_text"]


def test_question_to_chunk_url_defaults_to_empty_string_not_none():
    # Cohérent avec point_to_chunk (metadata Pinecone préfère une chaîne vide
    # à un None absent — filtrage/affichage plus simple côté lecture).
    chunk = index_qe.question_to_chunk(_q(source_url=None), "schaerbeek")
    assert chunk["metadata"]["url"] == ""
    chunk2 = index_qe.question_to_chunk(_q(source_url="https://1030.be/qe/015.pdf"), "schaerbeek")
    assert chunk2["metadata"]["url"] == "https://1030.be/qe/015.pdf"


def test_load_chunks_skips_entries_without_id(tmp_path):
    import json
    path = tmp_path / "qe.json"
    path.write_text(json.dumps({"questions": [_q(), {"annee": 2025, "numero": 1}]}), encoding="utf-8")
    chunks = index_qe.load_chunks(path, "schaerbeek")
    assert len(chunks) == 1
    assert chunks[0]["id"] == "QE-2025-015"
