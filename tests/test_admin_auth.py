"""Tests du flux d'authentification admin (/admin/login, /admin/me,
/admin/logout) : login correct/incorrect, session persistée via cookie,
expiration/altération du jeton, et absence de configuration côté serveur.
"""
from fastapi.testclient import TestClient

import app
from services.auth import hash_password, create_session_token, verify_session_token

client = TestClient(app.app)

USERNAME = "pierre"
PASSWORD = "un-mot-de-passe-suffisamment-long"


def _configure_admin(monkeypatch, username=USERNAME, password=PASSWORD, secret="test-secret-key"):
    monkeypatch.setenv("ADMIN_USERNAME", username)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password(password))
    if secret is not None:
        monkeypatch.setenv("ADMIN_JWT_SECRET", secret)
    else:
        monkeypatch.delenv("ADMIN_JWT_SECRET", raising=False)


def test_me_without_session_is_401():
    r = client.get("/admin/me")
    assert r.status_code == 401


def test_login_success_sets_cookie_and_me_succeeds(monkeypatch):
    _configure_admin(monkeypatch)
    # TestClient parle en http:// (pas de TLS) : un cookie Secure serait
    # rejeté par le cookie jar avant même d'atteindre /admin/me, comme dans un
    # vrai navigateur — même bascule que pour les tests locaux réels.
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    r = client.post("/admin/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "username": USERNAME}
    assert "pv_admin_session" in r.cookies

    r2 = client.get("/admin/me")
    assert r2.status_code == 200
    assert r2.json() == {"authenticated": True, "username": USERNAME}

    r3 = client.post("/admin/logout")
    assert r3.status_code == 200
    r4 = client.get("/admin/me")
    assert r4.status_code == 401


def test_login_wrong_password_rejected(monkeypatch):
    _configure_admin(monkeypatch)
    r = client.post("/admin/login", json={"username": USERNAME, "password": "mauvais-mot-de-passe"})
    assert r.status_code == 401
    assert "pv_admin_session" not in r.cookies


def test_login_wrong_username_rejected(monkeypatch):
    _configure_admin(monkeypatch)
    r = client.post("/admin/login", json={"username": "quelqu-un-d-autre", "password": PASSWORD})
    assert r.status_code == 401


def test_login_without_server_config_rejected(monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    r = client.post("/admin/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 401


def test_login_rejects_malformed_body():
    r = client.post("/admin/login", json={"username": ""})
    assert r.status_code == 422


def test_verify_session_token_rejects_tampered_signature(monkeypatch):
    monkeypatch.setenv("ADMIN_JWT_SECRET", "secret-a")
    token = create_session_token(USERNAME)
    payload_b64, _, _sig = token.partition(".")
    forged = payload_b64 + ".AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    assert verify_session_token(forged) is None


def test_verify_session_token_rejects_wrong_secret(monkeypatch):
    monkeypatch.setenv("ADMIN_JWT_SECRET", "secret-a")
    token = create_session_token(USERNAME)
    monkeypatch.setenv("ADMIN_JWT_SECRET", "secret-b")
    assert verify_session_token(token) is None


def test_verify_session_token_rejects_expired(monkeypatch):
    import services.auth as auth_module
    monkeypatch.setenv("ADMIN_JWT_SECRET", "secret-a")
    monkeypatch.setattr(auth_module, "SESSION_TTL_S", -1)
    token = create_session_token(USERNAME)
    assert verify_session_token(token) is None


def test_hash_password_roundtrip_and_salted():
    h1 = hash_password(PASSWORD)
    h2 = hash_password(PASSWORD)
    assert h1 != h2   # sels différents à chaque appel
    from services.auth import _verify_password
    assert _verify_password(PASSWORD, h1)
    assert _verify_password(PASSWORD, h2)
    assert not _verify_password("autre-chose", h1)
