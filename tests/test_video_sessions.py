"""Tests du lien « voir la séance » (vidéo) : intégrité de
`backend/video_sessions.json` et lecture par `_video_session_map` ; et du
comptage d'extraits par point (`video_chunk_counts`, backend/video_conseil_
schaerbeek.json) qui alimente « (N extraits) » à côté d'un lien vidéo.
"""
import json
import re
from pathlib import Path

from utils.video import video_session_map, video_chunk_counts

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "backend" / "video_sessions.json"
CHAPTERS = ROOT / "backend" / "video_conseil_schaerbeek.json"


def test_video_sessions_file_valid():
    """Chaque entrée : date ISO → URL YouTube de séance."""
    data = json.loads(SESSIONS.read_text(encoding="utf-8"))
    assert data, "au moins une séance filmée attendue"
    for date, url in data.items():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), f"date invalide : {date}"
        assert url.startswith("https://www.youtube.com/watch?v="), f"URL invalide : {url}"


def test_video_session_map_reads_file():
    m = video_session_map()
    assert isinstance(m, dict) and m
    assert m.get("2026-02-11", "").startswith("https://www.youtube.com/watch?v=")


def test_video_chunk_counts_reads_file():
    counts = video_chunk_counts()
    assert isinstance(counts, dict) and counts
    # Toutes les clés suivent le format "video-<video_id>-<start_s>" (même
    # format que les id de chunk Pinecone, voir index_pv / services.rag).
    for key, n in counts.items():
        assert re.fullmatch(r"video-.+-\d+", key), f"clé inattendue : {key}"
        assert isinstance(n, int) and n >= 1   # au moins le chunk « titre »


def test_video_chunk_counts_matches_source_data():
    """Recoupement direct avec le JSON source : un point avec K sous-chunks de
    transcript doit donner 1 + K dans le comptage."""
    data = json.loads(CHAPTERS.read_text(encoding="utf-8"))
    checked = 0
    for seance in data["seances"]:
        video_id = seance.get("video_id")
        if not video_id:
            continue
        for point in seance.get("points", []):
            transcript = point.get("transcript") or []
            if not transcript:
                continue
            key = f"video-{video_id}-{int(point['start_s'])}"
            assert video_chunk_counts().get(key) == 1 + len(transcript)
            checked += 1
    assert checked > 0, "aucun point avec transcript trouvé — le test ne vérifie rien"
