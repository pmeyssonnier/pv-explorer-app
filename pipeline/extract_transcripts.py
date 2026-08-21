# ══════════════════════════════════════════════════════════════════════════
#  ENRICHISSEMENT DES SÉANCES CHAPITRÉES AVEC LE TRANSCRIPT (sous-titres auto)
#
#  Suite du prototype (pipeline/prototype_transcript.py, validé sur un extrait
#  réel) : traite un LOT de séances déjà chapitrées, ajoute à chaque point
#  `point["transcript"]` = liste de sous-chunks {start_s, text} (le contenu
#  RÉEL du débat, découpé pour l'indexation Pinecone — voir index_pv.py
#  `video_point_to_chunks`, qui émet un chunk par sous-segment).
#
#  RAPPEL DES LIMITES (voir cartouche de prototype_transcript.py) : sous-titres
#  AUTO-GÉNÉRÉS (ASR) — patronymes parfois mal transcrits (pas fiable pour
#  l'attribution de parole, seulement pour la recherche de contenu) ; pas de
#  diarisation ; langue « -orig » unique par vidéo (réunions bilingues FR/NL).
#
#  À LANCER dans Google Colab, APRÈS avoir exécuté, dans l'ordre :
#    1. Cellules ①② de extract_video_chapters.ipynb (yt-dlp, _fetch_video_info)
#    2. Le chargement de pipeline/prototype_transcript.py (parse_vtt,
#       fetch_transcript_segments) — même mécanisme fetch-and-exec.
#    3. Ce script (idem), puis :
#         data = run_batch(min_date="2025-01-01")
#       → écrit /content/video_conseil_schaerbeek.json, prêt à committer.
# ══════════════════════════════════════════════════════════════════════════
import json
import urllib.request

BACKEND_RAW = "https://raw.githubusercontent.com/pmeyssonnier/pv-explorer-app/main/backend/"

# Taille cible d'un sous-chunk de transcript (caractères). Compromis entre
# cohérence sémantique du chunk et limite de longueur de l'embedding
# (multilingual-e5-large) — assez court pour rester focalisé sur un passage.
MAX_CHARS = 1500


def chunk_segments(segments, max_chars=MAX_CHARS):
    """Regroupe des segments (start_s, texte) consécutifs en sous-chunks
    d'environ `max_chars` caractères, sans jamais couper un segment en deux."""
    chunks = []
    cur_start, cur_text, cur_len = None, [], 0
    for start, text in segments:
        if cur_start is None:
            cur_start = start
        if cur_len and cur_len + len(text) + 1 > max_chars:
            chunks.append({"start_s": cur_start, "text": " ".join(cur_text)})
            cur_start, cur_text, cur_len = start, [], 0
        cur_text.append(text)
        cur_len += len(text) + 1
    if cur_text:
        chunks.append({"start_s": cur_start, "text": " ".join(cur_text)})
    return chunks


def enrich_seance_transcript(seance, lang="fr-orig"):
    """Ajoute point["transcript"] à chaque point de la séance, par alignement
    sur les sous-titres auto (voir prototype_transcript.align_transcript_to_points
    pour la logique de bucketing — ici on garde les sous-chunks au lieu d'un
    extrait tronqué unique). Liste vide si rien d'aligné pour un point."""
    segments = fetch_transcript_segments(seance["video_id"], lang=lang)  # noqa: F821 -- prototype_transcript.py
    points = sorted(seance["points"], key=lambda p: p["start_s"])
    for i, p in enumerate(points):
        lo = p["start_s"]
        hi = points[i + 1]["start_s"] if i + 1 < len(points) else float("inf")
        in_range = [(s, t) for s, t in segments if lo <= s < hi]
        p["transcript"] = chunk_segments(in_range)
    return seance


def run_batch(min_date="2025-01-01", lang="fr-orig"):
    """Enrichit toutes les séances chapitrées du dépôt dont la date >= min_date
    (séances SANS points ignorées — rien à aligner), fusionne avec les données
    actuelles, écrit le résultat prêt à committer dans /content/."""
    data = json.loads(urllib.request.urlopen(BACKEND_RAW + "video_conseil_schaerbeek.json").read())
    seances = data["seances"]
    todo = [s for s in seances if s["points"] and s["date"] >= min_date]
    print(f"{len(todo)} séances à enrichir (>= {min_date})")

    for i, s in enumerate(todo, 1):
        try:
            enrich_seance_transcript(s, lang=lang)
            n = sum(len(p.get("transcript") or []) for p in s["points"])
            print(f"  {i:>2}/{len(todo)}  {s['date']}  →  {n:>3} sous-chunks de transcript")
        except Exception as ex:                      # pas de sous-titres, bot-check résiduel…
            print(f"  ⚠ {s['date']} : échec ({str(ex)[:80]})")

    with open("/content/video_conseil_schaerbeek.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    n_total = sum(len(p.get("transcript") or []) for s in todo for p in s["points"])
    print(f"\n✅ {n_total} sous-chunks ajoutés sur {len(todo)} séances "
          f"→ /content/video_conseil_schaerbeek.json ({len(seances)} séances au total)")
    return data
