"""Tests de l'indexation du chapitrage vidéo (`index_pv.py`) : construction du
chunk « video_conseil » (deep-link, source_type, ID stable), des chunks de
transcript alignés (sous-titres auto), et détection du format vidéo par
`load_chunks`.
"""
import json

from index_pv import SCHEMA_VERSION, load_chunks, video_point_to_chunks


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
    chunks = video_point_to_chunks(_point(), _seance(), "schaerbeek")
    assert len(chunks) == 1                       # pas de transcript → un seul chunk (titre)
    m = chunks[0]["metadata"]
    assert m["source_type"] == "video_conseil"
    assert m["url"] == "https://www.youtube.com/watch?v=2Iv7zt2sSnA&t=10253s"
    assert m["date"] == "2026-02-11" and m["year"] == 2026
    assert m["sp"] == 0
    assert m["type"] == "motion" and m["auteur"] == "Elias AMMI"
    assert "Elias AMMI" in m["decision"]          # libellé ex. « Motion · Elias AMMI »
    assert "Elias AMMI" in m["chunk_text"]        # texte vectorisé mentionne l'auteur
    assert m["schema_version"] == SCHEMA_VERSION


def test_video_chunk_id_stable():
    """ID déterministe (video-<id>-<start>) → upsert idempotent."""
    c1 = video_point_to_chunks(_point(), _seance(), "schaerbeek")
    c2 = video_point_to_chunks(_point(), _seance(), "schaerbeek")
    assert c1[0]["id"] == c2[0]["id"] == "video-2Iv7zt2sSnA-10253"


def test_video_chunk_with_transcript_adds_extra_chunks():
    """point["transcript"] (sous-titres auto alignés) → un chunk de titre +
    un chunk par sous-segment, avec deep-link vers l'instant précis."""
    point = _point()
    point["transcript"] = [
        {"start_s": 10300, "text": "Merci monsieur le président, je voulais aborder..."},
        {"start_s": 10420, "text": "Et je pense que la solidarité avec le peuple iranien..."},
    ]
    chunks = video_point_to_chunks(point, _seance(), "schaerbeek")
    assert len(chunks) == 3                        # 1 titre + 2 sous-segments
    title, seg1, seg2 = chunks
    assert title["id"] == "video-2Iv7zt2sSnA-10253"
    assert seg1["id"] == "video-2Iv7zt2sSnA-10253-t10300"
    assert seg2["id"] == "video-2Iv7zt2sSnA-10253-t10420"
    assert seg1["metadata"]["url"] == "https://www.youtube.com/watch?v=2Iv7zt2sSnA&t=10300s"
    assert "solidarité avec le peuple iranien" in seg2["metadata"]["chunk_text"]
    # Les chunks de transcript restent groupés avec le point (même métadonnées).
    assert seg1["metadata"]["source_type"] == "video_conseil"
    assert seg1["metadata"]["auteur"] == "Elias AMMI"
    assert seg1["metadata"]["titre"] == title["metadata"]["titre"]


def test_video_chunk_ignores_empty_transcript_segments():
    point = _point()
    point["transcript"] = [{"start_s": 10300, "text": "   "}]   # vide après strip()
    chunks = video_point_to_chunks(point, _seance(), "schaerbeek")
    assert len(chunks) == 1                         # segment vide ignoré


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
