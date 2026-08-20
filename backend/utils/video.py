"""Carte date → URL de la vidéo de séance (backend/video_sessions.json).

Partagée par le RAG (/ask : liens « voir le débat / la séance » sur les sources)
et les statistiques (/stats : liste des PV). Lecture mtime-cachée. {} si absent.
"""
import json
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "video_sessions.json")
_cache: dict = {"mtime": None, "map": {}}


def video_session_map() -> dict:
    """date ISO → URL de la vidéo de la séance (début de séance)."""
    try:
        mtime = os.path.getmtime(_PATH)
    except OSError:
        return {}
    if _cache["mtime"] != mtime:
        try:
            with open(_PATH, encoding="utf-8") as f:
                _cache["map"] = json.load(f)
            _cache["mtime"] = mtime
        except Exception:
            return _cache["map"]
    return _cache["map"]
