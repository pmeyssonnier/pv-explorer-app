"""Tests de l'indexation du chapitrage vidéo (`index_pv.py`) : construction du
chunk « video_conseil » (deep-link, source_type, ID stable) et détection du
format vidéo par `load_chunks`.
"""
import json

from index_pv import load_chunks, video_point_to_chunk


def _seance():
    return {
        "date": "2026-02-11",
        "video_id": "2Iv7zt2sSnA",
        "video_url": "https://www.youtube.com/watch?v=2Iv7zt2sSnA",
        "points": [],
    }


def _point():
    return {
        "titre": "Motion ... (Motion de Monsieur Elias AMMI) - Motie ...",
        "titre_fr": "Motion en soutien au peuple iranien (Motion de Monsieur Elias AMMI)",
        "type": "motion", "auteur": "Elias AMMI",
        "start_s": 10253,
        "deeplink": "https://www.youtube.com/watch?v=2Iv7zt2sSnA&t=10253s",
    }


def test_video_chunk_metadata():
    c = video_point_to_chunk(_point(), _seance(), "schaerbeek")
    m = c["metadata"]
    assert m["source_type"] == "video_conseil"
    assert m["url"] == "https://www.youtube.com/watch?v=2Iv7zt2sSnA&t=10253s"
    assert m["date"] == "2026-02-11" and m["year"] == 2026
    assert m["sp"] == 0
    assert m["type"] == "motion" and m["auteur"] == "Elias AMMI"
    assert "Elias AMMI" in m["decision"]          # libellé ex. « Motion · Elias AMMI »
    assert "Elias AMMI" in m["chunk_text"]        # texte vectorisé mentionne l'auteur


def test_video_chunk_id_stable():
    """ID déterministe (video-<id>-<start>) → upsert idempotent."""
    c1 = video_point_to_chunk(_point(), _seance(), "schaerbeek")
    c2 = video_point_to_chunk(_point(), _seance(), "schaerbeek")
    assert c1["id"] == c2["id"] == "video-2Iv7zt2sSnA-10253"


def test_load_chunks_detects_video_format(tmp_path):
    """Un JSON avec `video_id` par séance → chunks « video_conseil »."""
    seance = _seance()
    seance["points"] = [_point()]
    db = {"source": "video_conseil_schaerbeek", "seances": [seance]}
    p = tmp_path / "video.json"
    p.write_text(json.dumps(db), encoding="utf-8")

    chunks = load_chunks(p, "schaerbeek")
    assert len(chunks) == 1
    assert chunks[0]["metadata"]["source_type"] == "video_conseil"
    assert chunks[0]["id"] == "video-2Iv7zt2sSnA-10253"


def test_load_chunks_still_reads_pv_format(tmp_path):
    """Un JSON PV classique reste traité comme des délibérations (source pv)."""
    db = {"seances": [{
        "seance": {"id": "PV-2020-01-29", "date": "2020-01-29"},
        "points": [{"sp": 12, "titre": "Rénovation", "decision": "APPROUVÉ"}],
    }]}
    p = tmp_path / "pv.json"
    p.write_text(json.dumps(db), encoding="utf-8")

    chunks = load_chunks(p, "schaerbeek")
    assert len(chunks) == 1
    # Le chunk PV ne porte pas source_type "video_conseil".
    assert chunks[0]["metadata"].get("source_type", "pv") != "video_conseil"
    assert chunks[0]["id"] == "PV-2020-01-29_SP12"
