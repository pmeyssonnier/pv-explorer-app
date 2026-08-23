"""Tests des routes /admin/questions-ecrites/{extract,publish} — même
approche que test_admin_seances.py : gating par require_admin, mapping
d'erreurs, forme de la réponse. services.questions_ecrites_integration est
monkeypatché avec des fonctions QUASI INSTANTANÉES (déjà couvert en détail
par test_questions_ecrites_integration.py) — les tâches tournent dans un
vrai thread (services/jobs.py réel, pas mocké), on sonde juste très vite."""
import time

import pytest
from fastapi.testclient import TestClient

import app
import routers.admin as admin_router
from limiter import limiter
from services.auth import hash_password

client = TestClient(app.app)

USERNAME = "pierre"
PASSWORD = "un-mot-de-passe-suffisamment-long"


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    limiter.reset()


def _login(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password(PASSWORD))
    monkeypatch.setenv("ADMIN_JWT_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    r = client.post("/admin/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def _wait_until_settled(path, max_wait=2.0):
    deadline = time.time() + max_wait
    r = client.get(path)
    while time.time() < deadline:
        if r.status_code != 200 or r.json().get("status") != "pending":
            return r
        time.sleep(0.02)
        r = client.get(path)
    return r


def test_extract_requires_admin_session():
    r = client.post("/admin/questions-ecrites/extract", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 401


def test_publish_requires_admin_session():
    r = client.post("/admin/questions-ecrites/publish", json={"question": {"id": "QE-2025-001"}})
    assert r.status_code == 401


def test_extract_status_requires_admin_session():
    r = client.get("/admin/questions-ecrites/extract/some-job-id")
    assert r.status_code == 401


def test_extract_status_unknown_job_is_404(monkeypatch):
    _login(monkeypatch)
    r = client.get("/admin/questions-ecrites/extract/does-not-exist")
    assert r.status_code == 404


def test_extract_rejects_empty_file(monkeypatch):
    _login(monkeypatch)
    r = client.post("/admin/questions-ecrites/extract", files={"file": ("x.pdf", b"", "application/pdf")})
    assert r.status_code == 400


def test_extract_returns_job_id_immediately(monkeypatch):
    _login(monkeypatch)

    def slow_ok(raw, filename):
        time.sleep(0.05)
        return {"question": {"id": "QE-2025-015"}, "preview": {"id": "QE-2025-015", "annee": 2025,
                                                                 "numero": 15, "is_new": True}}

    monkeypatch.setattr(admin_router.questions_ecrites_integration, "extract_and_preview", slow_ok)
    started = time.time()
    r = client.post("/admin/questions-ecrites/extract", files={"file": ("015.pdf", b"%PDF-1.4 ...", "application/pdf")})
    elapsed = time.time() - started
    assert r.status_code == 200
    assert "job_id" in r.json()
    assert elapsed < 0.05, "la route doit répondre AVANT la fin du traitement (tâche de fond)"


def test_extract_job_completes_and_reports_done(monkeypatch):
    _login(monkeypatch)
    fake_result = {"question": {"id": "QE-2025-015", "auteur": "Georges Verzin"},
                   "preview": {"id": "QE-2025-015", "annee": 2025, "numero": 15, "is_new": True}}
    monkeypatch.setattr(
        admin_router.questions_ecrites_integration, "extract_and_preview",
        lambda raw, filename: fake_result,
    )
    r = client.post("/admin/questions-ecrites/extract", files={"file": ("015.pdf", b"%PDF-1.4 ...", "application/pdf")})
    job_id = r.json()["job_id"]
    status = _wait_until_settled(f"/admin/questions-ecrites/extract/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "done"
    assert body["question"] == fake_result["question"]
    assert body["preview"]["is_new"] is True


def test_extract_status_includes_progress_while_pending(monkeypatch):
    _login(monkeypatch)
    import threading
    release = threading.Event()

    def slow_with_progress(raw, filename, progress_cb=None):
        progress_cb({"stage": "extraction"})
        release.wait(timeout=2.0)
        return {"question": {"id": "QE-2025-015"}, "preview": {"id": "QE-2025-015", "annee": 2025,
                                                                 "numero": 15, "is_new": True}}

    monkeypatch.setattr(admin_router.questions_ecrites_integration, "extract_and_preview", slow_with_progress)
    r = client.post("/admin/questions-ecrites/extract", files={"file": ("015.pdf", b"%PDF-1.4 ...", "application/pdf")})
    job_id = r.json()["job_id"]

    deadline = time.time() + 2.0
    status = client.get(f"/admin/questions-ecrites/extract/{job_id}")
    while time.time() < deadline and status.json().get("progress") is None:
        time.sleep(0.02)
        status = client.get(f"/admin/questions-ecrites/extract/{job_id}")

    assert status.status_code == 200
    assert status.json() == {"status": "pending", "progress": {"stage": "extraction"}}

    release.set()
    _wait_until_settled(f"/admin/questions-ecrites/extract/{job_id}")


def test_extract_job_maps_value_error_to_422(monkeypatch):
    _login(monkeypatch)

    def boom(raw, filename):
        raise ValueError("Extraction impossible : PDF scanné/image.")

    monkeypatch.setattr(admin_router.questions_ecrites_integration, "extract_and_preview", boom)
    r = client.post("/admin/questions-ecrites/extract", files={"file": ("015.pdf", b"%PDF-1.4 ...", "application/pdf")})
    job_id = r.json()["job_id"]
    status = _wait_until_settled(f"/admin/questions-ecrites/extract/{job_id}")
    assert status.status_code == 422
    assert "scanné" in status.json()["detail"]


def test_publish_returns_job_id_and_completes(monkeypatch):
    _login(monkeypatch)
    fake_result = {"commit_sha": "abc123", "id": "QE-2025-015", "auteur": "Georges Verzin"}
    monkeypatch.setattr(
        admin_router.questions_ecrites_integration, "publish_question",
        lambda question, source_url: fake_result,
    )
    r = client.post("/admin/questions-ecrites/publish", json={
        "question": {"id": "QE-2025-015", "numero": 15, "annee": 2025,
                     "date": "2025-11-10", "auteur": "Georges Verzin"},
        "source_url": "https://1030.be/qe/015.pdf",
    })
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    status = _wait_until_settled(f"/admin/questions-ecrites/publish/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "done"
    assert body["commit_sha"] == "abc123"


def test_publish_job_maps_value_error_to_422(monkeypatch):
    _login(monkeypatch)

    def boom(question, source_url):
        raise ValueError("Question incomplète (date/numéro/auteur·e manquant) — rien à publier.")

    monkeypatch.setattr(admin_router.questions_ecrites_integration, "publish_question", boom)
    r = client.post("/admin/questions-ecrites/publish", json={"question": {"id": "QE-2025-015"}})
    job_id = r.json()["job_id"]
    status = _wait_until_settled(f"/admin/questions-ecrites/publish/{job_id}")
    assert status.status_code == 422
