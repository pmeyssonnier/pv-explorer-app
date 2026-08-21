"""Tests du dédoublonnage des sources vidéo par POINT (`services/rag.py`).

Un point de chapitrage vidéo peut produire plusieurs chunks indexés (un
chunk « titre » + un chunk par sous-segment de transcript aligné — voir
index_pv.video_point_to_chunks), qui partagent le même id de base avec un
suffixe « -t<sous_start> » pour les extraits de transcript. `_point_key()`
regroupe ces chunks pour qu'une seule source soit affichée par point.
"""
from services.rag import _hit_id, _point_key


def test_point_key_strips_transcript_suffix():
    """Chunk titre et chunks transcript d'un même point → même clé."""
    assert _point_key("video-abc123-1140") == "video-abc123-1140"
    assert _point_key("video-abc123-1140-t1147") == "video-abc123-1140"
    assert _point_key("video-abc123-1140-t2300") == "video-abc123-1140"
    # Les trois id ci-dessus partagent bien la même clé de point.
    keys = {_point_key(i) for i in
            ["video-abc123-1140", "video-abc123-1140-t1147", "video-abc123-1140-t2300"]}
    assert len(keys) == 1


def test_point_key_no_effect_on_pv_ids():
    """Les id des points PV (un seul chunk, pas de suffixe -t<n>) sont inchangés."""
    assert _point_key("PV-2020-01-29_SP12") == "PV-2020-01-29_SP12"


def test_point_key_distinguishes_different_points():
    """Deux points distincts (start_s différent) → clés différentes."""
    assert _point_key("video-abc123-1140") != _point_key("video-abc123-2300")


def test_point_key_empty_is_safe():
    assert _point_key("") == ""
    assert _point_key(None) is None


def test_hit_id_from_dict_underscore_id():
    assert _hit_id({"_id": "video-abc123-1140-t1147", "score": 0.9}) == "video-abc123-1140-t1147"


def test_hit_id_from_dict_plain_id_fallback():
    assert _hit_id({"id": "video-abc123-1140", "score": 0.9}) == "video-abc123-1140"


def test_hit_id_from_object_attribute():
    class Hit:
        id = "video-abc123-1140-t2300"
    assert _hit_id(Hit()) == "video-abc123-1140-t2300"


def test_hit_id_missing_returns_empty_string():
    assert _hit_id({"score": 0.9}) == ""
