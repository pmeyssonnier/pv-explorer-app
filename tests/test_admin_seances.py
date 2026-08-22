"""Tests des routes /admin/seances/extract et /admin/seances/publish : gating
par require_admin, mapping d'erreurs, forme de la réponse. services.pv_integration
est monkeypatché — aucun appel réel à Claude/GitHub/Pinecone (déjà couvert par
test_pv_integration.py pour la logique elle-même)."""
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
    # Le limiter est un état process-global (partagé par tous les tests de la
    # session pytest) : sans reset, les logins des tests précédents (ici et
    # dans test_admin_auth.py) épuisent le quota 5/minute de /admin/login.
    limiter.reset()


def _login(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password(PASSWORD))
    monkeypatch.setenv("ADMIN_JWT_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    r = client.post("/admin/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def test_extract_requires_admin_session():
    r = client.post("/admin/seances/extract", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 401


def test_publish_requires_admin_session():
    r = client.post("/admin/seances/publish", json={"seance": {"date": "2026-06-24"}, "points": []})
    assert r.status_code == 401


def test_extract_rejects_empty_file(monkeypatch):
    _login(monkeypatch)
    r = client.post("/admin/seances/extract", files={"file": ("x.pdf", b"", "application/pdf")})
    assert r.status_code == 400


def test_extract_success(monkeypatch):
    _login(monkeypatch)
    fake_seance = {"seance": {"date": "2026-06-24"}, "points": [{"sp": 1, "titre": "Un point"}]}
    monkeypatch.setattr(
        admin_router.pv_integration, "extract_from_upload",
        lambda raw, filename: fake_seance,
    )
    monkeypatch.setattr(
        admin_router.pv_integration, "preview_merge",
        lambda s: {"date": "2026-06-24", "n_points": 1, "is_new": True, "existing_points": 0},
    )
    r = client.post("/admin/seances/extract", files={"file": ("pv.pdf", b"%PDF-1.4 ...", "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["seance"] == fake_seance
    assert body["preview"]["is_new"] is True


def test_extract_maps_value_error_to_422(monkeypatch):
    _login(monkeypatch)

    def boom(raw, filename):
        raise ValueError("PDF scanné/image")

    monkeypatch.setattr(admin_router.pv_integration, "extract_from_upload", boom)
    r = client.post("/admin/seances/extract", files={"file": ("pv.pdf", b"%PDF-1.4 ...", "application/pdf")})
    assert r.status_code == 422
    assert "scanné" in r.json()["detail"]


def test_extract_maps_runtime_error_to_500(monkeypatch):
    _login(monkeypatch)

    def boom(raw, filename):
        raise RuntimeError("ANTHROPIC_API_KEY manquante côté serveur")

    monkeypatch.setattr(admin_router.pv_integration, "extract_from_upload", boom)
    r = client.post("/admin/seances/extract", files={"file": ("pv.pdf", b"%PDF-1.4 ...", "application/pdf")})
    assert r.status_code == 500


def test_publish_success(monkeypatch):
    _login(monkeypatch)
    monkeypatch.setattr(
        admin_router.pv_integration, "publish_seance",
        lambda seance, source_url: {"commit_sha": "abc123", "date": "2026-06-24", "n_points": 1, "indexed": 1},
    )
    r = client.post("/admin/seances/publish", json={
        "seance": {"seance": {"date": "2026-06-24"}, "points": [{"sp": 1}]},
        "source_url": "https://1030.be/pv.pdf",
    })
    assert r.status_code == 200
    assert r.json()["commit_sha"] == "abc123"


def test_publish_maps_value_error_to_422(monkeypatch):
    _login(monkeypatch)

    def boom(seance, source_url):
        raise ValueError("Séance sans date ou sans points — rien à publier.")

    monkeypatch.setattr(admin_router.pv_integration, "publish_seance", boom)
    r = client.post("/admin/seances/publish", json={"seance": {"seance": {}, "points": []}})
    assert r.status_code == 422
