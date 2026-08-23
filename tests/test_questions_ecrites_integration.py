"""Tests de services/questions_ecrites_integration.py et services/questions_ecrites.py.

Aucun appel réel à Claude/GitHub/Pinecone : qe_pipeline.process_pdf,
commit_file et l'indexation Pinecone sont monkeypatchés — même approche que
test_pv_integration.py, ces tests vérifient l'orchestration (fusion,
injection de source_url, résumé renvoyé, réindexation), pas les services
externes eux-mêmes.
"""
import json

import pytest

import services.questions_ecrites as qe_service
import services.questions_ecrites_integration as qe_integration


def _fake_question(numero=15, annee=2025, auteur="Georges Verzin", reponse="Réponse."):
    return {
        "id": f"QE-{annee}-{numero:03d}", "annee": annee, "numero": numero,
        "date": f"{annee}-11-10", "auteur": auteur, "titre": "Les nids-de-poule",
        "question": "Quand seront-ils réparés ?", "reponse": reponse,
        "source_file": "test.pdf", "extracted_at": "2026-01-01T00:00:00",
    }


@pytest.fixture
def fake_qe_db_path(tmp_path, monkeypatch):
    db = {
        "meta": {"nom": "test", "version": "1.0", "total_questions": 1},
        "questions": [_fake_question(numero=3, annee=2024, auteur="Ancien·ne")],
    }
    path = tmp_path / "questions_ecrites_schaerbeek.json"
    path.write_text(json.dumps(db), encoding="utf-8")
    monkeypatch.setattr(qe_service, "QE_JSON_PATH", str(path))
    monkeypatch.setattr(qe_integration, "QE_JSON_PATH", str(path))
    qe_service._cache["mtime"] = None   # invalide le cache module (autre chemin)
    return path


# ── services/questions_ecrites.py : chargement + cache ──────────────────────
def test_load_qe_db_returns_empty_db_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(qe_service, "QE_JSON_PATH", str(tmp_path / "does-not-exist.json"))
    db = qe_service.load_qe_db()
    assert db == qe_service._EMPTY_DB
    assert db["questions"] == []


def test_load_qe_db_reads_existing_file(fake_qe_db_path):
    db = qe_service.load_qe_db()
    assert db["meta"]["total_questions"] == 1
    assert db["questions"][0]["auteur"] == "Ancien·ne"


def test_load_qe_db_cache_invalidated_on_mtime_change(fake_qe_db_path):
    db1 = qe_service.load_qe_db()
    assert len(db1["questions"]) == 1
    # Réécrit le fichier (mtime différent) : le cache doit se rafraîchir,
    # pas retourner la version périmée en mémoire.
    import os
    import time
    time.sleep(0.01)
    new_content = json.dumps({"meta": {"total_questions": 2}, "questions": [
        _fake_question(numero=1), _fake_question(numero=2),
    ]})
    fake_qe_db_path.write_text(new_content, encoding="utf-8")
    os.utime(fake_qe_db_path, None)
    db2 = qe_service.load_qe_db()
    assert len(db2["questions"]) == 2


# ── extract_from_upload / extract_and_preview ────────────────────────────────
def test_extract_from_upload_writes_temp_file_and_returns_struct(monkeypatch):
    captured = {}

    def fake_process_pdf(pdf_path, progress_cb=None):
        captured["path"] = pdf_path
        captured["exists"] = pdf_path.exists()
        return _fake_question()

    monkeypatch.setattr(qe_integration.qe_pipeline, "process_pdf", fake_process_pdf)
    result = qe_integration.extract_from_upload(b"%PDF-1.4 fake bytes", "015.verzin.pdf")

    assert captured["exists"] is True
    assert captured["path"].name == "015.verzin.pdf"
    assert result["id"] == "QE-2025-015"


def test_extract_from_upload_forwards_progress_cb(monkeypatch):
    captured = {}

    def fake_process_pdf(pdf_path, progress_cb=None):
        captured["progress_cb"] = progress_cb
        if progress_cb:
            progress_cb({"stage": "extraction"})
        return _fake_question()

    monkeypatch.setattr(qe_integration.qe_pipeline, "process_pdf", fake_process_pdf)
    reports = []
    qe_integration.extract_from_upload(b"x", "x.pdf", progress_cb=reports.append)

    assert captured["progress_cb"] is not None
    assert reports == [{"stage": "extraction"}]


def test_extract_from_upload_raises_on_extraction_failure(monkeypatch):
    monkeypatch.setattr(qe_integration.qe_pipeline, "process_pdf", lambda p, progress_cb=None: None)
    with pytest.raises(ValueError):
        qe_integration.extract_from_upload(b"not a real pdf", "empty.pdf")


def test_extract_from_upload_sanitizes_path_traversal(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        qe_integration.qe_pipeline, "process_pdf",
        lambda p, progress_cb=None: captured.setdefault("name", p.name) or _fake_question()
    )
    qe_integration.extract_from_upload(b"x", "../../etc/passwd.pdf")
    assert captured["name"] == "passwd.pdf"


def test_extract_and_preview_combines_extraction_and_merge_preview(monkeypatch, fake_qe_db_path):
    monkeypatch.setattr(qe_integration.qe_pipeline, "process_pdf", lambda p, progress_cb=None: _fake_question())
    result = qe_integration.extract_and_preview(b"x", "x.pdf")
    assert result["question"]["id"] == "QE-2025-015"
    assert result["preview"] == {"id": "QE-2025-015", "annee": 2025, "numero": 15, "is_new": True}


# ── preview_merge ─────────────────────────────────────────────────────────────
def test_preview_merge_detects_new_question(fake_qe_db_path):
    preview = qe_integration.preview_merge(_fake_question(numero=15, annee=2025))
    assert preview == {"id": "QE-2025-015", "annee": 2025, "numero": 15, "is_new": True}


def test_preview_merge_detects_existing_question(fake_qe_db_path):
    # La question fixture existante est QE-2024-003 (voir fake_qe_db_path).
    preview = qe_integration.preview_merge(_fake_question(numero=3, annee=2024))
    assert preview["is_new"] is False


# ── publish_question ──────────────────────────────────────────────────────────
def test_publish_question_rejects_incomplete_question():
    with pytest.raises(ValueError):
        qe_integration.publish_question({"numero": 15, "auteur": "X"})   # date manquante
    with pytest.raises(ValueError):
        qe_integration.publish_question({"date": "2025-11-10", "auteur": "X"})   # numero manquant
    with pytest.raises(ValueError):
        qe_integration.publish_question({"date": "2025-11-10", "numero": 15})   # auteur manquant


def test_publish_question_merges_and_commits(fake_qe_db_path, monkeypatch):
    commit_calls = []

    def fake_commit_file(path, content, message):
        commit_calls.append((path, json.loads(content), message))
        return "abc123"

    monkeypatch.setattr(qe_integration, "commit_file", fake_commit_file)
    monkeypatch.setattr(qe_integration, "_index_question", lambda question: None)

    result = qe_integration.publish_question(
        _fake_question(numero=15, annee=2025), source_url="https://1030.be/qe/015.pdf",
    )

    assert result == {"commit_sha": "abc123", "id": "QE-2025-015", "auteur": "Georges Verzin"}
    assert len(commit_calls) == 1
    committed_path, committed_content, committed_message = commit_calls[0]
    assert committed_path.endswith("questions_ecrites_schaerbeek.json")
    assert "15" in committed_message and "Georges Verzin" in committed_message
    ids = {q["id"] for q in committed_content["questions"]}
    assert ids == {"QE-2024-003", "QE-2025-015"}   # fusion, pas d'écrasement de l'existante
    new_q = next(q for q in committed_content["questions"] if q["id"] == "QE-2025-015")
    assert new_q["source_url"] == "https://1030.be/qe/015.pdf"
    assert committed_content["meta"]["total_questions"] == 2


def test_publish_question_reports_commit_then_indexing_progress(fake_qe_db_path, monkeypatch):
    monkeypatch.setattr(qe_integration, "commit_file", lambda path, content, message: "abc123")
    monkeypatch.setattr(qe_integration, "_index_question", lambda question: None)
    reports = []
    qe_integration.publish_question(_fake_question(), progress_cb=reports.append)
    assert reports == [{"stage": "commit"}, {"stage": "indexing"}]


def test_publish_question_indexes_via_index_qe(fake_qe_db_path, monkeypatch):
    monkeypatch.setattr(qe_integration, "commit_file", lambda path, content, message: "abc123")
    fake_pc = object()
    monkeypatch.setattr(qe_integration, "get_pinecone_client", lambda: fake_pc)
    calls = []
    monkeypatch.setattr(qe_integration.index_qe, "index_chunks", lambda pc, chunks: calls.append((pc, chunks)))

    question = _fake_question(numero=15, annee=2025)
    qe_integration.publish_question(question)

    assert len(calls) == 1
    pc, chunks = calls[0]
    assert pc is fake_pc
    assert len(chunks) == 1
    assert chunks[0]["id"] == "QE-2025-015"
    assert chunks[0]["metadata"]["source_type"] == "question_ecrite"


def test_publish_question_leaves_source_url_none_when_not_provided(fake_qe_db_path, monkeypatch):
    monkeypatch.setattr(qe_integration, "_index_question", lambda question: None)
    captured = {}

    def fake_commit_file(path, content, message):
        captured["content"] = json.loads(content)
        return "abc123"

    monkeypatch.setattr(qe_integration, "commit_file", fake_commit_file)
    qe_integration.publish_question(_fake_question(numero=99))
    new_q = next(q for q in captured["content"]["questions"] if q["numero"] == 99)
    assert new_q.get("source_url") is None
