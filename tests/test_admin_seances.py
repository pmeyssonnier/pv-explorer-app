"""Tests des routes /admin/seances/{extract,publish} (démarrage + statut de
tâche de fond, voir services/jobs.py) : gating par require_admin, mapping
d'erreurs, forme de la réponse. services.pv_integration est monkeypatché avec
des fonctions QUASI INSTANTANÉES (aucun appel réel à Claude/GitHub/Pinecone,
déjà couvert par test_pv_integration.py) — les tâches tournent dans un vrai
thread (services/jobs.py réel, pas mocké), on sonde juste très vite."""
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
    # État process-global partagé entre tous les tests de la session pytest —
    # sans reset, les logins des tests précédents (ici et test_admin_auth.py)
    # épuisent le quota 5/minute de /admin/login.
    limiter.reset()


def _login(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password(PASSWORD))
    monkeypatch.setenv("ADMIN_JWT_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    r = client.post("/admin/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def _wait_until_settled(path, max_wait=2.0):
    """Sonde `path` jusqu'à ce que le statut ne soit plus "pending" (la
    fonction mockée est quasi instantanée — le thread de fond a largement le
    temps de finir dans la fenêtre de 2s) ou jusqu'au plafond."""
    deadline = time.time() + max_wait
    r = client.get(path)
    while time.time() < deadline:
        if r.status_code != 200 or r.json().get("status") != "pending":
            return r
        time.sleep(0.02)
        r = client.get(path)
    return r


def test_extract_requires_admin_session():
    r = client.post("/admin/seances/extract", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 401


def test_publish_requires_admin_session():
    r = client.post("/admin/seances/publish", json={"seance": {"date": "2026-06-24"}, "points": []})
    assert r.status_code == 401


def test_extract_status_requires_admin_session():
    r = client.get("/admin/seances/extract/some-job-id")
    assert r.status_code == 401


def test_extract_status_unknown_job_is_404(monkeypatch):
    _login(monkeypatch)
    r = client.get("/admin/seances/extract/does-not-exist")
    assert r.status_code == 404


def test_extract_rejects_empty_file(monkeypatch):
    _login(monkeypatch)
    r = client.post("/admin/seances/extract", files={"file": ("x.pdf", b"", "application/pdf")})
    assert r.status_code == 400


def test_extract_returns_job_id_immediately(monkeypatch):
    _login(monkeypatch)

    def slow_ok(raw, filename):
        time.sleep(0.05)
        return {"seance": {"date": "2026-06-24"}, "preview": {"date": "2026-06-24", "n_points": 1,
                                                                "is_new": True, "existing_points": 0}}

    monkeypatch.setattr(admin_router.pv_integration, "extract_and_preview", slow_ok)
    started = time.time()
    r = client.post("/admin/seances/extract", files={"file": ("pv.pdf", b"%PDF-1.4 ...", "application/pdf")})
    elapsed = time.time() - started
    assert r.status_code == 200
    assert "job_id" in r.json()
    assert elapsed < 0.05, "la route doit répondre AVANT la fin du traitement (tâche de fond)"


def test_extract_job_completes_and_reports_done(monkeypatch):
    _login(monkeypatch)
    fake_result = {"seance": {"date": "2026-06-24"}, "preview": {"date": "2026-06-24", "n_points": 1,
                                                                   "is_new": True, "existing_points": 0}}
    monkeypatch.setattr(admin_router.pv_integration, "extract_and_preview", lambda raw, filename: fake_result)
    r = client.post("/admin/seances/extract", files={"file": ("pv.pdf", b"%PDF-1.4 ...", "application/pdf")})
    job_id = r.json()["job_id"]
    status = _wait_until_settled(f"/admin/seances/extract/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "done"
    assert body["seance"] == fake_result["seance"]
    assert body["preview"]["is_new"] is True


def test_extract_job_maps_value_error_to_422(monkeypatch):
    _login(monkeypatch)

    def boom(raw, filename):
        raise ValueError("PDF scanné/image")

    monkeypatch.setattr(admin_router.pv_integration, "extract_and_preview", boom)
    r = client.post("/admin/seances/extract", files={"file": ("pv.pdf", b"%PDF-1.4 ...", "application/pdf")})
    job_id = r.json()["job_id"]
    status = _wait_until_settled(f"/admin/seances/extract/{job_id}")
    assert status.status_code == 422
    assert "scanné" in status.json()["detail"]


def test_extract_job_maps_runtime_error_to_500(monkeypatch):
    _login(monkeypatch)

    def boom(raw, filename):
        raise RuntimeError("ANTHROPIC_API_KEY manquante côté serveur")

    monkeypatch.setattr(admin_router.pv_integration, "extract_and_preview", boom)
    r = client.post("/admin/seances/extract", files={"file": ("pv.pdf", b"%PDF-1.4 ...", "application/pdf")})
    job_id = r.json()["job_id"]
    status = _wait_until_settled(f"/admin/seances/extract/{job_id}")
    assert status.status_code == 500


def test_publish_returns_job_id_and_completes(monkeypatch):
    _login(monkeypatch)
    fake_result = {"commit_sha": "abc123", "date": "2026-06-24", "n_points": 1, "indexed": 1}
    monkeypatch.setattr(
        admin_router.pv_integration, "publish_seance",
        lambda seance, source_url: fake_result,
    )
    r = client.post("/admin/seances/publish", json={
        "seance": {"seance": {"date": "2026-06-24"}, "points": [{"sp": 1}]},
        "source_url": "https://1030.be/pv.pdf",
    })
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    status = _wait_until_settled(f"/admin/seances/publish/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "done"
    assert body["commit_sha"] == "abc123"


def test_publish_job_maps_value_error_to_422(monkeypatch):
    _login(monkeypatch)

    def boom(seance, source_url):
        raise ValueError("Séance sans date ou sans points — rien à publier.")

    monkeypatch.setattr(admin_router.pv_integration, "publish_seance", boom)
    r = client.post("/admin/seances/publish", json={"seance": {"seance": {}, "points": []}})
    job_id = r.json()["job_id"]
    status = _wait_until_settled(f"/admin/seances/publish/{job_id}")
    assert status.status_code == 422
