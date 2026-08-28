"""Tests de services.people.mandats — parsing des plages de mandats
déclaratives (elus_mandats.json) et classification du rôle par date.
Fonctions pures testées directement (_parse_ranges/_year_in_ranges) ;
role_at/mandats_for testées via un jeu de données monkeypatché (bypasse le
cache par mtime du vrai fichier, indépendant de son contenu réel). Les
vérifications sur les VRAIES données (élu·e·s réel·le·s, /elu/{key}) vivent
dans test_elus.py, à côté des autres tests utilisant la base réelle.

Édition (list_mandats/save_mandat + endpoints /admin/mandats) testée en fin
de fichier — même schéma que test_lexique.py : _MANDATS_PATH redirigé vers
un fichier temporaire, jamais le vrai elus_mandats.json.
"""
import json

import pytest
from fastapi.testclient import TestClient

import app
import services.people.mandats as mandats
from limiter import limiter
from services import github_publish
from services.auth import hash_password

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


# ── _parse_ranges : "AAAA-AAAA" / "AAAA-présent", virgules, annotations ─────
def test_parse_ranges_single_closed_range():
    assert mandats._parse_ranges("2018-2024") == [(2018, 2024)]


def test_parse_ranges_open_ended_present():
    assert mandats._parse_ranges("2024-présent") == [(2024, None)]


def test_parse_ranges_multiple_comma_separated():
    assert mandats._parse_ranges("2012-2019, 2024-présent") == [(2012, 2019), (2024, None)]


def test_parse_ranges_strips_parenthetical_annotation():
    assert mandats._parse_ranges("2019-2024 (faisant fonction)") == [(2019, 2024)]
    assert mandats._parse_ranges("2025-présent (en titre; empêchée en 2026)") == [(2025, None)]


def test_parse_ranges_none_or_empty_string():
    assert mandats._parse_ranges(None) == []
    assert mandats._parse_ranges("") == []


def test_parse_ranges_ignores_unrecognized_segment():
    # Donnée déclarative externe : un segment malformé est ignoré, jamais
    # d'exception qui ferait planter tout le classement de rôle.
    assert mandats._parse_ranges("pas une date") == []
    assert mandats._parse_ranges("2018-2024, pas une date") == [(2018, 2024)]


# ── _year_in_ranges ──────────────────────────────────────────────────────────
def test_year_in_ranges_open_ended_covers_any_future_year():
    # Une plage "-présent" doit couvrir automatiquement une future
    # législature (ex. 2030) sans qu'aucun code ne change.
    assert mandats._year_in_ranges(2030, [(2024, None)]) is True
    assert mandats._year_in_ranges(2100, [(2024, None)]) is True


def test_year_in_ranges_closed_range_excludes_before_and_after():
    assert mandats._year_in_ranges(2017, [(2018, 2024)]) is False
    assert mandats._year_in_ranges(2020, [(2018, 2024)]) is True
    assert mandats._year_in_ranges(2025, [(2018, 2024)]) is False


# ── role_at : précédence Collège, repli None, dates absentes/invalides ─────
def test_role_at_unknown_person_returns_none(monkeypatch):
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {})
    assert mandats.role_at("inconnu", "2020-01-01") is None


def test_role_at_missing_or_empty_date_returns_none(monkeypatch):
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {
        "x": {"conseiller": [(2000, None)], "echevin": [], "bourgmestre": []},
    })
    assert mandats.role_at("x", None) is None
    assert mandats.role_at("x", "") is None


def test_role_at_college_takes_precedence(monkeypatch):
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {
        "x": {"conseiller": [(2000, None)], "echevin": [(2020, None)], "bourgmestre": []},
    })
    # Échevin·e ET conseiller·ère en même temps (cas normal, les échevin·e·s
    # restent conseiller·ère·s) : le Collège l'emporte pour le classement.
    assert mandats.role_at("x", "2021-01-01") == "college"
    # Avant le mandat d'échevin : simple conseiller·ère.
    assert mandats.role_at("x", "2010-01-01") == "conseiller"
    # Avant même le mandat de conseiller·ère : rôle inconnu à cette date.
    assert mandats.role_at("x", "1995-01-01") is None


def test_role_at_bourgmestre_also_counts_as_college(monkeypatch):
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {
        "x": {"conseiller": [(2000, None)], "echevin": [], "bourgmestre": [(2019, 2024)]},
    })
    assert mandats.role_at("x", "2020-01-01") == "college"
    assert mandats.role_at("x", "2025-01-01") == "conseiller"


# ── Intégrité des VRAIES données : chaque segment doit être reconnu ────────
# Cas vécu : une annotation entre parenthèses contenant elle-même une virgule
# (ex. « 2001-2024 (empêché 2008-2011 et 2019-2024, remplacé par ... ) »)
# coupe le split(",") de _parse_ranges en plein milieu de la parenthèse — le
# segment ne matche plus _RANGE_RE et disparaît SILENCIEUSEMENT (Bernard
# Clerfayt n'était alors jamais classé « college », toute sa carrière de
# bourgmestre invisible pour role_at()). Un test sur les vraies données,
# jamais une virgule interdite dans une annotation.
def test_real_mandats_every_range_segment_is_recognized():
    for e in mandats._load_mandats_raw():
        for champ in ("conseiller_communal", "echevin", "bourgmestre"):
            raw = e.get(champ)
            if not raw:
                continue
            for part in raw.split(","):
                assert mandats._RANGE_RE.match(part), (
                    f"{e.get('nom')} / {champ} : segment non reconnu « {part.strip()} » "
                    f"dans « {raw} » — une virgule dans une annotation entre parenthèses "
                    "casse le split(\",\") (utiliser un point-virgule à la place)"
                )


# ── mandats_for : structure exposée pour l'affichage détaillé ──────────────
def test_mandats_for_returns_none_when_person_absent():
    assert mandats.mandats_for("inconnu") is None


def test_mandats_for_returns_none_when_all_ranges_empty(monkeypatch):
    # Ex. personne listée mais jamais élue (statut "Non élu / Candidat").
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {
        "x": {"conseiller": [], "echevin": [], "bourgmestre": []},
    })
    assert mandats.mandats_for("x") is None


def test_mandats_for_shapes_ranges_as_debut_fin_dicts(monkeypatch):
    monkeypatch.setattr(mandats, "_mandats_by_key", lambda: {
        "x": {"conseiller": [(2012, 2019), (2024, None)], "echevin": [(2025, None)], "bourgmestre": []},
    })
    assert mandats.mandats_for("x") == {
        "conseiller": [{"debut": 2012, "fin": 2019}, {"debut": 2024, "fin": None}],
        "echevin": [{"debut": 2025, "fin": None}],
    }


# ── Édition (panneau admin) : list_mandats/save_mandat ──────────────────────
# Le fichier réel n'est jamais touché : _MANDATS_PATH redirigé vers un
# fichier temporaire, même schéma que tmp_lexique dans test_lexique.py.
@pytest.fixture
def tmp_mandats(tmp_path, monkeypatch):
    p = tmp_path / "elus_mandats.json"
    p.write_text(json.dumps([
        {"nom": "Alice Dupont", "conseiller_communal": "2012-présent",
         "echevin": None, "bourgmestre": None, "statut": "Conseillère communale"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mandats, "_MANDATS_PATH", str(p))
    monkeypatch.setattr(mandats, "_cache", {"mtime": None, "by_key": None})
    return p


def test_list_mandats_returns_raw_entries(tmp_mandats):
    got = mandats.list_mandats()
    assert len(got) == 1
    assert got[0]["nom"] == "Alice Dupont"


def test_save_mandat_updates_existing_entry_by_name(tmp_mandats):
    entry = mandats.save_mandat("Alice Dupont", "2012-présent", "2020-présent", None, "Échevine")
    assert entry == {
        "nom": "Alice Dupont", "conseiller_communal": "2012-présent",
        "echevin": "2020-présent", "bourgmestre": None, "statut": "Échevine",
    }
    got = mandats.list_mandats()
    assert len(got) == 1   # mise à jour, pas un doublon
    assert got[0]["echevin"] == "2020-présent"


def test_save_mandat_creates_new_entry_when_name_unknown(tmp_mandats):
    mandats.save_mandat("Bob Martin", "2024-présent", None, None, "Conseiller communal")
    got = mandats.list_mandats()
    assert len(got) == 2
    assert {e["nom"] for e in got} == {"Alice Dupont", "Bob Martin"}


def test_save_mandat_renames_entry_via_nom_original(tmp_mandats):
    # L'admin corrige le nom lui-même (coquille) : doit RENOMMER l'entrée
    # existante, pas en créer une seconde à côté de l'ancienne.
    mandats.save_mandat("Alice Duponnt", "2012-présent", None, None, None,
                         nom_original="Alice Dupont")
    got = mandats.list_mandats()
    assert len(got) == 1
    assert got[0]["nom"] == "Alice Duponnt"


def test_save_mandat_rejects_empty_name(tmp_mandats):
    with pytest.raises(ValueError):
        mandats.save_mandat("", "2012-présent", None, None, None)


def test_save_mandat_rejects_malformed_range(tmp_mandats):
    with pytest.raises(ValueError):
        mandats.save_mandat("Alice Dupont", "pas une date", None, None, None)


def test_save_mandat_takes_effect_immediately_on_role_at(tmp_mandats):
    # Écriture locale (pas seulement le commit distant, monkeypatché par
    # l'appelant HTTP) : role_at() doit refléter le changement tout de suite,
    # via le cache par mtime de _mandats_by_key — pas seulement conseillère
    # (donnée de départ de la fixture), déjà "college" après l'ajout échevin.
    assert mandats.role_at("dupont", "2021-01-01") == "conseiller"
    mandats.save_mandat("Alice Dupont", "2012-présent", "2020-présent", None, "Échevine")
    assert mandats.role_at("dupont", "2021-01-01") == "college"


def test_delete_mandat_removes_entry_and_returns_it(tmp_mandats):
    removed = mandats.delete_mandat("Alice Dupont")
    assert removed["nom"] == "Alice Dupont"
    assert mandats.list_mandats() == []


def test_delete_mandat_takes_effect_immediately_on_role_at(tmp_mandats):
    assert mandats.role_at("dupont", "2021-01-01") == "conseiller"
    mandats.delete_mandat("Alice Dupont")
    assert mandats.role_at("dupont", "2021-01-01") is None


def test_delete_mandat_rejects_empty_name(tmp_mandats):
    with pytest.raises(ValueError):
        mandats.delete_mandat("")


def test_delete_mandat_rejects_unknown_name(tmp_mandats):
    with pytest.raises(ValueError):
        mandats.delete_mandat("Personne Inconnue")
    # Le fichier n'a pas été touché par la tentative infructueuse.
    assert len(mandats.list_mandats()) == 1


# ── Endpoints /admin/mandats (auth + commit monkeypatché) ───────────────────
def _login(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password(PASSWORD))
    monkeypatch.setenv("ADMIN_JWT_SECRET", "test-secret-key")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    r = client.post("/admin/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def test_mandats_endpoints_require_admin():
    assert client.get("/admin/mandats").status_code == 401
    assert client.post("/admin/mandats", json={"nom": "Alice Dupont"}).status_code == 401


def test_post_mandats_saves_and_commits(tmp_mandats, monkeypatch):
    commits = []
    monkeypatch.setattr(github_publish, "commit_file",
                        lambda path, content, message: commits.append((path, json.loads(content), message)) or "sha123")
    _login(monkeypatch)
    r = client.post("/admin/mandats", json={
        "nom": "Alice Dupont", "conseiller_communal": "2012-présent",
        "echevin": "2020-présent", "statut": "Échevine",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["committed"] is True
    assert body["mandat"]["echevin"] == "2020-présent"
    # Le commit a reçu le fichier complet à jour, au bon chemin.
    assert commits and commits[0][0] == "backend/elus_mandats.json"
    assert commits[0][1][0]["echevin"] == "2020-présent"
    g = client.get("/admin/mandats")
    assert g.status_code == 200
    assert g.json()["mandats"][0]["echevin"] == "2020-présent"
    client.post("/admin/logout")


def test_post_mandats_bad_range_is_400(tmp_mandats, monkeypatch):
    monkeypatch.setattr(github_publish, "commit_file", lambda *a, **k: "sha")
    _login(monkeypatch)
    r = client.post("/admin/mandats", json={"nom": "Alice Dupont", "echevin": "pas une date"})
    assert r.status_code == 400
    client.post("/admin/logout")


def test_delete_mandats_requires_admin():
    assert client.request("DELETE", "/admin/mandats", params={"nom": "Alice Dupont"}).status_code == 401


def test_delete_mandats_removes_and_commits(tmp_mandats, monkeypatch):
    commits = []
    monkeypatch.setattr(github_publish, "commit_file",
                        lambda path, content, message: commits.append((path, json.loads(content), message)) or "sha123")
    _login(monkeypatch)
    r = client.request("DELETE", "/admin/mandats", params={"nom": "Alice Dupont"})
    assert r.status_code == 200
    body = r.json()
    assert body["committed"] is True
    assert body["mandat"]["nom"] == "Alice Dupont"
    # Le commit a reçu le fichier complet à jour (sans l'entrée supprimée).
    assert commits and commits[0][0] == "backend/elus_mandats.json"
    assert commits[0][1] == []
    g = client.get("/admin/mandats")
    assert g.json()["mandats"] == []
    client.post("/admin/logout")


def test_delete_mandats_unknown_name_is_400(tmp_mandats, monkeypatch):
    monkeypatch.setattr(github_publish, "commit_file", lambda *a, **k: "sha")
    _login(monkeypatch)
    r = client.request("DELETE", "/admin/mandats", params={"nom": "Personne Inconnue"})
    assert r.status_code == 400
    client.post("/admin/logout")
