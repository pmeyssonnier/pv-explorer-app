"""Tests du lien « voir la séance » (vidéo) : intégrité de
`backend/video_sessions.json` et lecture par `_video_session_map` ; et du
comptage d'extraits par point (`video_chunk_counts`, backend/video_conseil_
schaerbeek.json) qui alimente « (N extraits) » à côté d'un lien vidéo.
"""
import datetime
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


# ── Chapitrage : une entrée par SÉANCE, pas par vidéo ──
def test_une_seule_entree_video_par_date():
    # Le fichier source suit les enregistrements : une séance filmée en
    # plusieurs fois y occupe plusieurs entrées, et les appelants prenaient la
    # première venue (le 29/03/2023, la « SUITE » et ses 8 chapitres, perdant
    # les 24 de l'enregistrement principal). _load_video les regroupe.
    from services.people.registry import _load_video
    dates = [s["date"] for s in _load_video()]
    assert dates
    assert len(dates) == len(set(dates)), "deux entrées pour une même date"


def test_le_regroupement_ne_perd_aucun_chapitre():
    import json
    from services.people import registry
    brut = json.load(open(registry._VIDEO_PATH, encoding="utf-8"))
    brut = brut.get("seances", brut) if isinstance(brut, dict) else brut
    attendu = sum(len(v.get("points") or []) for v in brut)
    obtenu = sum(len(v.get("points") or []) for v in registry._load_video())
    assert obtenu == attendu, "des chapitres disparaissent au regroupement"


def test_une_video_du_lendemain_rejoint_la_seance_du_pv():
    # Une séance prolongée après minuit est titrée AU LENDEMAIN (« Conseil
    # communal du 26/11/2020 » pour le PV du 25/11) : sans recalage, elle
    # apparaissait comme une séance fantôme, sans PV, à côté de la vraie.
    from services.people.registry import _load_video
    from services.statistics import load_db
    pv = {(s.get("seance") or {}).get("date") for s in load_db().get("seances", [])}
    dates = {s["date"] for s in _load_video()}
    assert "2020-11-25" in dates and "2020-11-26" not in dates
    assert "2020-12-16" in dates and "2020-12-17" not in dates
    # Le recalage ne remonte que d'un jour, et jamais sur une date qui a déjà
    # son PV : il ne peut donc pas déplacer une séance réelle.
    for d in dates - pv:
        veille = (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat()
        assert veille not in pv, f"{d} aurait dû être recalée sur {veille}"


def test_les_deux_enregistrements_dune_seance_sont_tous_deux_atteignables():
    # 29/03/2023 : l'enregistrement principal ET sa suite. Les chapitres
    # portent leur propre deeplink (video_id inclus), donc les réunir sous une
    # seule date ne mélange aucun lien — la séance mène bien aux deux vidéos.
    from services import seances as svc
    detail = svc.seance_detail("2023-03-29")
    ids = {str(p["video_url"]).split("v=")[1].split("&")[0]
           for p in detail["points"] if p.get("video_url")}
    assert len(ids) >= 2, ids
