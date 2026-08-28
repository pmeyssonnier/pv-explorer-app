"""Tests du lexique éditable (lexique_store) et de ses endpoints admin
(/admin/lexique). Le fichier réel n'est jamais touché : _PATH est redirigé vers
un fichier temporaire, et le commit distant (github_publish) est monkeypatché.
"""
import json

import pytest
from fastapi.testclient import TestClient

import app
import lexique_store
from limiter import limiter
from services import github_publish
from services.auth import hash_password
from services.rag import _glossaire_block
from utils.text import _canon_theme, _decision_status

client = TestClient(app.app)

USERNAME = "pierre"
PASSWORD = "un-mot-de-passe-suffisamment-long"


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    # État process-global partagé entre tous les tests de la session pytest
    # (même mécanique que test_admin_seances.py/test_admin_questions_ecrites.py)
    # — sans reset, les logins des tests précédents (ici et dans les autres
    # fichiers admin) épuisent le quota 5/minute de /admin/login.
    limiter.reset()


@pytest.fixture
def tmp_lexique(tmp_path, monkeypatch):
    """Redirige lexique_store vers un fichier temporaire vierge + cache neuf."""
    p = tmp_path / "lexique.json"
    monkeypatch.setattr(lexique_store, "_PATH", str(p))
    monkeypatch.setattr(lexique_store, "_cache", {"mtime": None, "data": None})
    return p


# ── lexique_store (fonctions pures) ─────────────────────────────────────────
def test_load_missing_file_returns_empty_sections(tmp_lexique):
    data = lexique_store.load()
    assert set(data) == {"thematiques", "decisions", "personnes", "extraction", "glossaire"}
    assert data["personnes"] == {"alias": {}, "noms": {}}
    assert data["extraction"]["retrait"] == []


def test_add_entry_map_kinds(tmp_lexique):
    lexique_store.add_entry("theme", "sports", "sport")
    lexique_store.add_entry("decision", "approbation", "Approuvé")
    lexique_store.add_entry("alias", "erlay", "eraly")
    lexique_store.add_entry("nom", "malingreau", "Alain Malingreau")
    lexique_store.add_entry("def", "dropzone", "Périmètre de cyclopartage")
    assert lexique_store.thematiques()["sports"] == "sport"
    assert lexique_store.decisions()["approbation"] == "Approuvé"
    assert lexique_store.person_aliases()["erlay"] == "eraly"
    assert lexique_store.person_names()["malingreau"] == "Alain Malingreau"
    assert lexique_store.glossaire()["dropzone"].startswith("Périmètre")
    # Persisté sur le disque (fichier temporaire) → relecture après invalidation.
    lexique_store._cache["mtime"] = None
    assert lexique_store.load()["thematiques"]["sports"] == "sport"


def test_add_entry_list_kind_dedups(tmp_lexique):
    lexique_store.add_entry("retrait", "", "ôté de la séance")
    lexique_store.add_entry("retrait", "", "ôté de la séance")  # doublon ignoré
    assert lexique_store.extraction_phrases("retrait") == ["ôté de la séance"]


def test_add_entry_rejects_unknown_kind_and_empty(tmp_lexique):
    with pytest.raises(ValueError):
        lexique_store.add_entry("inconnu", "x", "y")
    with pytest.raises(ValueError):
        lexique_store.add_entry("theme", "", "y")
    with pytest.raises(ValueError):
        lexique_store.add_entry("retrait", "", "")


# ── Application en LECTURE (par-dessus les constantes en dur) ────────────────
def test_theme_override_applied_by_canon_theme(tmp_lexique):
    lexique_store.add_entry("theme", "velo", "mobilite")  # mapping arbitraire (pas une singularisation)
    assert _canon_theme("velo") == "mobilite"


def test_decision_synonym_applied_by_decision_status(tmp_lexique):
    lexique_store.add_entry("decision", "approbation", "Approuvé")
    assert _decision_status("approbation") == "Approuvé"   # absent des libellés en dur


def test_glossaire_block_injects_only_matching_terms(tmp_lexique):
    lexique_store.add_entry("def", "dropzone", "Périmètre de stationnement en flotte libre.")
    # Terme présent dans la question (insensible casse/accents) → bloc <glossaire>.
    block = _glossaire_block("Où sont les DROPZONES à Schaerbeek ?", "")
    assert "<glossaire>" in block
    assert "dropzone" in block and "flotte libre" in block
    # Terme présent seulement dans les extraits → également injecté.
    assert "<glossaire>" in _glossaire_block("météo", "… installer une dropzone rue X …")
    # Aucun terme du glossaire → pas de bloc.
    assert _glossaire_block("Quel est le budget voirie ?", "extrait sans jargon") == ""


# ── Endpoints /admin/lexique (auth + commit monkeypatché) ───────────────────
def _login(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password(PASSWORD))
    monkeypatch.setenv("ADMIN_JWT_SECRET", "test-secret-key")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    r = client.post("/admin/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def test_lexique_endpoints_require_admin():
    assert client.get("/admin/lexique").status_code == 401
    assert client.post("/admin/lexique", json={"kind": "theme", "key": "a", "value": "b"}).status_code == 401


def test_post_lexique_adds_and_commits(tmp_lexique, monkeypatch):
    commits = []
    monkeypatch.setattr(github_publish, "commit_file",
                        lambda path, content, message: commits.append((path, json.loads(content), message)) or "sha123")
    _login(monkeypatch)
    r = client.post("/admin/lexique", json={"kind": "def", "key": "dropzone", "value": "Périmètre cyclopartage"})
    assert r.status_code == 200
    body = r.json()
    assert body["committed"] is True
    assert body["lexique"]["glossaire"]["dropzone"] == "Périmètre cyclopartage"
    # Le commit a reçu le lexique à jour, au bon chemin.
    assert commits and commits[0][0] == "backend/lexique.json"
    assert commits[0][1]["glossaire"]["dropzone"] == "Périmètre cyclopartage"
    # GET reflète l'ajout.
    g = client.get("/admin/lexique")
    assert g.status_code == 200
    assert g.json()["glossaire"]["dropzone"] == "Périmètre cyclopartage"
    client.post("/admin/logout")


def test_post_lexique_bad_kind_is_400(tmp_lexique, monkeypatch):
    monkeypatch.setattr(github_publish, "commit_file", lambda *a, **k: "sha")
    _login(monkeypatch)
    r = client.post("/admin/lexique", json={"kind": "n_importe_quoi", "key": "a", "value": "b"})
    assert r.status_code == 400
    client.post("/admin/logout")
