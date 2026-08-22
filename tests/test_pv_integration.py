"""Tests de services/pv_integration.py et services/github_publish.py.

Aucun appel réel à Claude/GitHub/Pinecone : process_pdf, commit_file et
l'indexation Pinecone sont monkeypatchés — ces tests vérifient l'orchestration
(fusion, injection de source_url, résumé renvoyé), pas les services externes
eux-mêmes (déjà couverts ailleurs ou volontairement non testés en CI, coût réel).
"""
import json
from unittest.mock import MagicMock

import pytest

import services.pv_integration as pv_integration


def _fake_seance(date="2026-06-24", sp_values=(1, 2)):
    return {
        "seance": {"id": f"PV-{date}", "date": date, "source_file": "test.pdf",
                   "extraction_check": {"ok": True, "expected": len(sp_values),
                                        "extracted": len(sp_values), "missing_sp": []}},
        "points": [{"sp": sp, "type": "point", "titre": f"Point {sp}",
                    "thematiques": [], "montant_eur": None} for sp in sp_values],
    }


@pytest.fixture
def fake_db_path(tmp_path, monkeypatch):
    db = {
        "meta": {"nom": "test", "version": "1.0", "seances_incluses": [], "total_points": 0},
        "seances": [
            {"seance": {"id": "PV-2026-05-27", "date": "2026-05-27", "source_file": "x.pdf"},
             "points": [{"sp": 1, "type": "point", "titre": "Ancien point", "thematiques": []}]},
        ],
    }
    path = tmp_path / "pv_conseil_schaerbeek.json"
    path.write_text(json.dumps(db), encoding="utf-8")
    monkeypatch.setattr(pv_integration, "PV_JSON_PATH", str(path))
    return path


def test_extract_from_upload_writes_temp_file_and_returns_struct(monkeypatch):
    captured = {}

    def fake_process_pdf(pdf_path):
        captured["path"] = pdf_path
        captured["exists"] = pdf_path.exists()
        return _fake_seance()

    monkeypatch.setattr(pv_integration.pipeline, "process_pdf", fake_process_pdf)
    result = pv_integration.extract_from_upload(b"%PDF-1.4 fake bytes", "pv-conseil-2026.06.24-sp.pdf")

    assert captured["exists"] is True
    assert captured["path"].name == "pv-conseil-2026.06.24-sp.pdf"
    assert result["seance"]["date"] == "2026-06-24"


def test_extract_from_upload_raises_on_extraction_failure(monkeypatch):
    monkeypatch.setattr(pv_integration.pipeline, "process_pdf", lambda p: None)
    with pytest.raises(ValueError):
        pv_integration.extract_from_upload(b"not a real pdf", "empty.pdf")


def test_extract_from_upload_sanitizes_path_traversal(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        pv_integration.pipeline, "process_pdf",
        lambda p: captured.setdefault("name", p.name) or _fake_seance()
    )
    pv_integration.extract_from_upload(b"x", "../../etc/passwd.pdf")
    assert captured["name"] == "passwd.pdf"   # Path(...).name retire toute composante de répertoire


def test_preview_merge_detects_new_seance(fake_db_path):
    preview = pv_integration.preview_merge(_fake_seance(date="2026-06-24"))
    assert preview == {"date": "2026-06-24", "n_points": 2, "is_new": True, "existing_points": 0}


def test_preview_merge_detects_existing_seance(fake_db_path):
    preview = pv_integration.preview_merge(_fake_seance(date="2026-05-27", sp_values=(1, 2, 3)))
    assert preview["is_new"] is False
    assert preview["existing_points"] == 1


def test_publish_seance_rejects_missing_date_or_points():
    with pytest.raises(ValueError):
        pv_integration.publish_seance({"seance": {"date": None}, "points": []})
    with pytest.raises(ValueError):
        pv_integration.publish_seance({"seance": {"date": "2026-06-24"}, "points": []})


def test_publish_seance_merges_commits_and_indexes(fake_db_path, monkeypatch):
    commit_calls = []
    index_calls = []

    def fake_commit_file(path, content, message):
        commit_calls.append((path, json.loads(content), message))
        return "abc123"

    monkeypatch.setattr(pv_integration, "commit_file", fake_commit_file)
    monkeypatch.setattr(pv_integration, "_index_seance_points", lambda s: index_calls.append(s) or len(s["points"]))

    result = pv_integration.publish_seance(_fake_seance(date="2026-06-24"), source_url="https://1030.be/pv.pdf")

    assert result == {"commit_sha": "abc123", "date": "2026-06-24", "n_points": 2, "indexed": 2}
    assert len(commit_calls) == 1
    committed_path, committed_content, committed_message = commit_calls[0]
    assert committed_path.endswith("pv_conseil_schaerbeek.json")
    assert "2026-06-24" in committed_message
    dates = {s["seance"]["date"] for s in committed_content["seances"]}
    assert dates == {"2026-05-27", "2026-06-24"}
    new_seance = next(s for s in committed_content["seances"] if s["seance"]["date"] == "2026-06-24")
    assert new_seance["seance"]["source_url"] == "https://1030.be/pv.pdf"
    assert committed_content["meta"]["total_points"] == 3
    assert len(index_calls) == 1


# ── github_publish.commit_file (httpx mocké, aucun appel réseau réel) ──

def _mock_response(json_body, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    if status >= 400:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError("err", request=None, response=resp)
    return resp


def test_commit_file_missing_token_raises(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    import services.github_publish as gh
    with pytest.raises(RuntimeError):
        gh.commit_file("backend/x.json", "{}", "msg")


def test_commit_file_full_sequence(monkeypatch):
    import services.github_publish as gh
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
    monkeypatch.setenv("GITHUB_REPO", "acme/repo")
    monkeypatch.setenv("GITHUB_BRANCH", "main")

    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get.side_effect = [
        _mock_response({"object": {"sha": "base-commit-sha"}}),
        _mock_response({"tree": {"sha": "base-tree-sha"}}),
    ]
    client.post.side_effect = [
        _mock_response({"sha": "blob-sha"}),
        _mock_response({"sha": "new-tree-sha"}),
        _mock_response({"sha": "new-commit-sha"}),
    ]
    client.patch.return_value = _mock_response({"ref": "refs/heads/main"})

    monkeypatch.setattr(gh.httpx, "Client", lambda **kw: client)

    sha = gh.commit_file("backend/pv_conseil_schaerbeek.json", '{"a":1}', "data: test")

    assert sha == "new-commit-sha"
    assert client.get.call_count == 2
    assert client.post.call_count == 3
    client.patch.assert_called_once()
    patch_args = client.patch.call_args
    assert patch_args.kwargs["json"]["sha"] == "new-commit-sha"
