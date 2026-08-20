"""Tests du lien « voir la séance » (vidéo) : intégrité de
`backend/video_sessions.json` et lecture par `_video_session_map`.
"""
import json
import re
from pathlib import Path

from services.rag import _video_session_map

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "backend" / "video_sessions.json"


def test_video_sessions_file_valid():
    """Chaque entrée : date ISO → URL YouTube de séance."""
    data = json.loads(SESSIONS.read_text(encoding="utf-8"))
    assert data, "au moins une séance filmée attendue"
    for date, url in data.items():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), f"date invalide : {date}"
        assert url.startswith("https://www.youtube.com/watch?v="), f"URL invalide : {url}"


def test_video_session_map_reads_file():
    m = _video_session_map()
    assert isinstance(m, dict) and m
    assert m.get("2026-02-11", "").startswith("https://www.youtube.com/watch?v=")
