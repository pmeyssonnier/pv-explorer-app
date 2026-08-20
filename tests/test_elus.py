"""Tests de l'agrégation « Interventions par élu·e » (services.elus) et de ses
endpoints /elus et /elu/{key}.

Ces fonctions sont déterministes (lecture de la base JSON des PV + chapitrage
vidéo, aucun embedding) : on vérifie la normalisation des noms, la distinction
des rôles (auteur·e / répondant·e) et les invariants d'attribution.
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

import app
from services import elus

client = TestClient(app.app)
ROOT = Path(__file__).resolve().parent.parent


# ── Normalisation des noms ───────────────────────────────────────────────────
def test_key_matches_pv_surname_and_video_fullname():
    # « Georges VERZIN » (vidéo, majuscules) et « Verzin » (PV) → même clé.
    assert elus._key("Georges VERZIN") == "verzin"
    assert elus._key("Verzin") == "verzin"
    assert elus._key("Mme Nyssens") == "nyssens"
    assert elus._key("Noël, Bourgmestre") == "noel"


def test_titlecase_particles_and_caps():
    assert elus._titlecase("DEGREZ") == "Degrez"
    assert elus._titlecase("Yvan de Beauffort") == "Yvan de Beauffort"


def test_respondents_splits_compounds_and_resolves_roles():
    seance = {"bourgmestre": "Bernard Clerfayt", "bourgmestre_ff": "Cécile Jodogne"}
    # « X et le Bourgmestre » → X + le bourgmestre nommé (via la séance).
    names = elus._respondents("De Herde et M. le Bourgmestre", seance)
    keys = {elus._key(n) for n in names}
    assert "herde" in keys and "clerfayt" in keys
    # Rôle seul « la Bourgmestre ff » → résolu en Jodogne.
    assert elus._key(elus._respondents("la Bourgmestre ff", seance)[0]) == "jodogne"


# ── Liste des élu·e·s ────────────────────────────────────────────────────────
def test_elus_list_shape_and_sort():
    lst = elus.elus_list()
    assert lst and isinstance(lst, list)
    for e in lst:
        assert set(e) >= {"key", "nom", "role", "depose", "repond"}
        assert e["role"] in ("conseiller", "college")
    totals = [e["depose"] + e["repond"] for e in lst]
    assert totals == sorted(totals, reverse=True)  # tri par activité décroissante


# ── Détail : distinction des rôles ───────────────────────────────────────────
def test_verzin_is_conseiller_with_expected_activity():
    d = elus.elu_detail("verzin")
    assert d is not None
    assert d["role"] == "conseiller"
    c = d["counts"]
    # Agrégation structurée bien plus exhaustive que la recherche sémantique.
    assert c["questions"] >= 25
    assert c["demandes"] >= 20
    assert c["videos"] >= 1
    # INVARIANT clé : les motions ne sont attribuées que si l'auteur·e est nommé·e
    # dans le titre — on ne devine jamais depuis les intervenants (motions souvent
    # collectives). Verzin n'a donc aucune motion faussement attribuée.
    assert c["motions"] == 0


def test_detail_items_sorted_recent_first():
    d = elus.elu_detail("verzin")
    dates = [it["date"] for it in d["depose"]]
    assert dates == sorted(dates, reverse=True)
    # Chaque item déposé porte un libellé de type lisible.
    assert all(it["type_label"] for it in d["depose"])


def test_echevin_has_college_role_and_answers():
    # Un membre du Collège répond beaucoup et dépose peu → rôle « college ».
    d = elus.elu_detail("nimal")
    assert d is not None
    assert d["role"] == "college"
    assert d["counts"]["repond"] > d["counts"]["depose"]


def test_case_insensitive_key():
    assert elus.elu_detail("VERZIN")["key"] == "verzin"


# ── Endpoints HTTP ───────────────────────────────────────────────────────────
def test_endpoint_elus_list():
    r = client.get("/elus")
    assert r.status_code == 200
    assert len(r.json()["elus"]) > 10


def test_endpoint_elu_detail_and_404():
    r = client.get("/elu/verzin")
    assert r.status_code == 200
    assert r.json()["nom"] == "Georges Verzin"
    assert client.get("/elu/nom-inexistant-xyz").status_code == 404


# ── Intégrité du fichier de chapitrage vidéo ─────────────────────────────────
def test_video_chapters_file_valid():
    path = ROOT / "backend" / "video_conseil_schaerbeek.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    seances = data.get("seances", data) if isinstance(data, dict) else data
    assert seances, "au moins une séance filmée chapitrée attendue"
    # Au moins un point porte un auteur ET un deep-link horodaté.
    pts = [p for s in seances for p in s.get("points", [])]
    assert any(p.get("auteur") for p in pts)
    assert any((p.get("deeplink") or "").startswith("http") for p in pts)
