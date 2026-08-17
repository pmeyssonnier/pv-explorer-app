"""Tests de câblage HTTP : montage des routers + traduction des erreurs par la
couche endpoint. Exerce les vraies routes via TestClient (sans clé API : /,
/health et les agrégations JSON /stats /trend n'appellent ni Pinecone ni Claude).
Aurait attrapé une régression « routers non montés ».
"""
from fastapi.testclient import TestClient

import app

client = TestClient(app.app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_shape_minimal():
    # Réponse PUBLIQUE minimale : {status, index}. Sans PINECONE_API_KEY,
    # index="error" mais la route répond 200 (dégradé propre).
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["index"] in ("ok", "error")
    # Garde-fou vie privée : aucun détail fournisseur/volume ne doit fuiter.
    for leak in ("anthropic_key", "pinecone_key", "index_vectors", "index_ok"):
        assert leak not in body


def test_stats_route():
    r = client.get("/stats")
    assert r.status_code == 200
    j = r.json()
    assert j["nb_seances"] > 100
    assert j["nb_points"] > 1000
    assert "pv_par_annee" in j and "themes_par_annee" in j


def test_trend_route():
    r = client.get("/trend", params={"theme": "sport"})
    assert r.status_code == 200
    assert "annees" in r.json()


def test_trend_generique_400():
    # Thème sans radical exploitable → ValueError côté service → 400 côté route.
    r = client.get("/trend", params={"theme": "le"})
    assert r.status_code == 400
