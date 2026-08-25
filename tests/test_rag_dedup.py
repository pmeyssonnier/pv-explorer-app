"""Tests du dédoublonnage des sources vidéo par POINT (`services/rag.py`).

Un point de chapitrage vidéo peut produire plusieurs chunks indexés (un
chunk « titre » + un chunk par sous-segment de transcript aligné — voir
index_pv.video_point_to_chunks), qui partagent le même id de base avec un
suffixe « -t<sous_start> » pour les extraits de transcript. `_point_key()`
regroupe ces chunks pour qu'une seule source soit affichée par point.
"""
from services.rag import _hit_id, _point_key, _point_video_url


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


def test_point_video_url_from_title_chunk_key():
    assert (_point_video_url("video-sA80qmUc9VY-2125")
            == "https://www.youtube.com/watch?v=sA80qmUc9VY&t=2125s")


def test_point_video_url_same_for_any_transcript_subchunk_of_the_point():
    """Peu importe QUEL sous-chunk a été le mieux classé, le lien reconstruit
    pointe toujours vers le DÉBUT du point (pas le sous-segment lui-même)."""
    for chunk_id in [
        "video-sA80qmUc9VY-2125",
        "video-sA80qmUc9VY-2125-t2252",
        "video-sA80qmUc9VY-2125-t3364",
    ]:
        assert (_point_video_url(_point_key(chunk_id))
                == "https://www.youtube.com/watch?v=sA80qmUc9VY&t=2125s")


def test_point_video_url_handles_hyphenated_video_id():
    """Un video_id YouTube peut lui-même contenir des tirets (ex. -UIRlJYM26M) —
    le regex glouton doit quand même isoler correctement le start_s final."""
    assert (_point_video_url("video--UIRlJYM26M-3240")
            == "https://www.youtube.com/watch?v=-UIRlJYM26M&t=3240s")


def test_point_video_url_none_for_pv_id():
    assert _point_video_url("PV-2020-01-29_SP12") is None


def test_point_video_url_none_for_empty():
    assert _point_video_url("") is None
    assert _point_video_url(None) is None


# ── Noms d'une métadonnée : lus tels quels, jamais déduits ──
def test_noms_tolere_les_formes_dune_metadonnee():
    from services.rag import _noms
    assert _noms(["Saït Köse", "Elias Ammi"]) == ["Saït Köse", "Elias Ammi"]
    # Vecteur indexé avant SCHEMA_VERSION 2 : le champ n'existe pas. Personne
    # de nommé pour ce rôle — surtout pas un repli sur un autre champ.
    assert _noms(None) == []
    assert _noms([]) == []
    # Métadonnée réécrite à la main, ou source non-PV : une chaîne unique reste
    # un nom, pas une suite de lettres.
    assert _noms("Abobakre Bouhjar") == ["Abobakre Bouhjar"]
    assert _noms(["", None, "Elias Ammi"]) == ["Elias Ammi"]


def test_le_modele_source_expose_les_trois_roles():
    from models.api import Source
    s = Source(date="2025-06-25", sp=60, titre="T", decision="Approuvé", score=0.9)
    # Vides par défaut : une source qui ne nomme personne n'invente rien.
    assert s.auteurs == [] and s.intervenants == [] and s.repondants == []
    s2 = Source(date="2025-06-25", sp=60, titre="T", decision="Approuvé", score=0.9,
                auteurs=["Saït Köse"], repondants=["Abobakre Bouhjar"])
    assert s2.auteurs == ["Saït Köse"] and s2.repondants == ["Abobakre Bouhjar"]
