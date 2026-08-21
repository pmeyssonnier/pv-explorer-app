"""Cartes lues depuis les fichiers vidéo committés dans backend/ :
  - date ISO → URL de la vidéo de séance (video_sessions.json)
  - clé de point → nombre d'extraits de transcript indexés (video_conseil_schaerbeek.json)

Partagées par le RAG (/ask : liens « voir le débat / la séance » sur les
sources) et les statistiques (/stats : liste des PV). Lecture mtime-cachée.
{} si absent.
"""
import json
import os

_BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
_PATH = os.path.join(_BACKEND_DIR, "video_sessions.json")
_cache: dict = {"mtime": None, "map": {}}

_CHAPTERS_PATH = os.path.join(_BACKEND_DIR, "video_conseil_schaerbeek.json")
_chunk_counts_cache: dict = {"mtime": None, "map": {}}


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


def video_chunk_counts() -> dict:
    """« video-<video_id>-<start_s> » (même format que les id de chunk Pinecone,
    voir index_pv.video_point_to_chunks / services.rag._point_key) → nombre
    total d'extraits indexés pour ce point (1 chunk « titre » + un par
    sous-segment de transcript aligné). Sert à afficher « (N extraits) » à
    côté d'un lien « ▶ voir le débat », SANS prétendre compter des
    intervenant·e·s (aucune diarisation — un extrait est un découpage par
    tranche de texte, indépendant des changements de personne qui parle)."""
    try:
        mtime = os.path.getmtime(_CHAPTERS_PATH)
    except OSError:
        return {}
    if _chunk_counts_cache["mtime"] != mtime:
        try:
            with open(_CHAPTERS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            counts = {}
            for seance in data.get("seances", []):
                video_id = seance.get("video_id")
                if not video_id:
                    continue
                for point in seance.get("points", []):
                    start = int(point.get("start_s") or 0)
                    n = 1 + len(point.get("transcript") or [])
                    counts[f"video-{video_id}-{start}"] = n
            _chunk_counts_cache["map"] = counts
            _chunk_counts_cache["mtime"] = mtime
        except Exception:
            return _chunk_counts_cache["map"]
    return _chunk_counts_cache["map"]
