# ══════════════════════════════════════════════════════════════════════════
#  PROTOTYPE — Alignement transcript ↔ chapitrage (UNE séance)
#
#  Teste la faisabilité d'enrichir chaque point chapitré avec un extrait du
#  DÉBAT RÉEL, à partir des sous-titres AUTOMATIQUES YouTube (ASR, ex. "fr-orig").
#
#  ⚠️ PROTOTYPE, non intégré à l'app : ne touche ni Pinecone ni les fichiers
#  committés dans backend/. Sert uniquement à évaluer la qualité avant de
#  décider d'un déploiement complet (extension du pipeline + réindexation).
#
#  LIMITES CONNUES (constatées sur un échantillon réel, séance sA80qmUc9VY) :
#    - Sous-titres AUTO-GÉNÉRÉS (ASR) : les patronymes des élu·e·s sont souvent
#      mal transcrits/tronqués → NE PAS s'en servir pour l'attribution de
#      parole. Utile seulement pour la RECHERCHE DE CONTENU du débat.
#    - PAS de diarisation (qui parle) : un extrait est associé à un POINT de
#      l'ordre du jour (par plage de temps entre deux points chapitrés), pas
#      à une personne précise qui parle.
#    - Réunion bilingue FR/NL : la langue « -orig » auto-détectée est unique
#      par vidéo ; les passages dans l'autre langue peuvent être mal transcrits.
#    - Sous-titres « roulants » : chaque bloc VTT répète en partie le texte du
#      bloc précédent (fenêtre glissante) → dédupliqué ici par texte exact.
#
#  À LANCER dans Google Colab, APRÈS avoir exécuté les cellules ①② de
#  pipeline/extract_video_chapters.ipynb (yt-dlp installé, _fetch_video_info
#  chargé dans l'espace de noms du notebook) :
#      seance   = fetch_points_for_date("2026-05-27")   # points déjà chapitrés (dépôt)
#      segments = fetch_transcript_segments("sA80qmUc9VY")
#      enriched = align_transcript_to_points(seance, segments)
#      print_report(enriched)
# ══════════════════════════════════════════════════════════════════════════
import json
import re
import urllib.request

BACKEND_RAW = "https://raw.githubusercontent.com/pmeyssonnier/pv-explorer-app/main/backend/"

# Longueur max d'un extrait stocké par point (caractères) — évite un excès de
# texte dans l'aperçu (les points sans borne suivante vont jusqu'à la fin).
EXCERPT_MAX_CHARS = 1200

_VTT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})")


def fetch_points_for_date(date):
    """Points déjà chapitrés pour cette date, depuis le dépôt (source unique —
    committé via le notebook d'extraction, pas re-téléchargé ici)."""
    data = json.loads(urllib.request.urlopen(BACKEND_RAW + "video_conseil_schaerbeek.json").read())
    seance = next((s for s in data["seances"] if s["date"] == date), None)
    if not seance:
        raise ValueError(f"Aucune séance chapitrée pour {date} dans le dépôt.")
    return seance


def _vtt_seconds(ts):
    h, m, s, ms = _VTT_TIME.match(ts).groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_vtt(vtt_text):
    """VTT (sous-titres auto) → liste de segments (start_s, texte) triés et
    DÉDUPLIQUÉS (fenêtre glissante — voir cartouche ci-dessus)."""
    segments = []
    seen = set()
    for block in vtt_text.split("\n\n"):
        lines = block.strip().splitlines()
        ts_line = next((line for line in lines if "-->" in line), None)
        if not ts_line:
            continue
        start = _vtt_seconds(ts_line.split("-->")[0].strip())
        text = " ".join(
            re.sub(r"<[^>]+>", "", line).strip()
            for line in lines
            if "-->" not in line and line.strip()
        ).strip()
        if not text or text in seen:          # bloc répété par le sous-titrage roulant
            continue
        seen.add(text)
        segments.append((start, text))
    segments.sort(key=lambda x: x[0])
    return segments


def fetch_transcript_segments(video_id, lang="fr-orig"):
    """Télécharge et parse les sous-titres auto d'une vidéo (voir parse_vtt)."""
    v = _fetch_video_info(video_id)  # noqa: F821 -- fourni par extract_video_chapters.py (cellule ②)
    subs = (v.get("automatic_captions") or {}).get(lang) or (v.get("subtitles") or {}).get(lang)
    if not subs:
        raise ValueError(f"Pas de sous-titres « {lang} » disponibles pour {video_id}.")
    vtt_url = next(s["url"] for s in subs if s["ext"] == "vtt")
    vtt_text = urllib.request.urlopen(vtt_url).read().decode("utf-8")
    return parse_vtt(vtt_text)


def align_transcript_to_points(seance, segments):
    """Découpe le transcript par point : tout ce qui est dit entre le début
    d'un point et le début du suivant lui est attribué (bucketing simple,
    aucune diarisation). Ce qui précède le 1er point (banter d'ouverture,
    appel nominal…) est volontairement exclu."""
    points = sorted(seance["points"], key=lambda p: p["start_s"])
    enriched = []
    for i, p in enumerate(points):
        lo = p["start_s"]
        hi = points[i + 1]["start_s"] if i + 1 < len(points) else float("inf")
        texte = " ".join(t for s, t in segments if lo <= s < hi)
        enriched.append({**p, "extrait": texte[:EXCERPT_MAX_CHARS]})
    return enriched


def print_report(enriched):
    """Aperçu lisible : titre du point + début de l'extrait aligné, pour
    évaluer la qualité avant toute décision d'intégration."""
    for p in enriched:
        who = f" · {p['auteur']}" if p.get("auteur") else ""
        print(f"\n[{p['start_s'] // 60:>3}min] {p['type']}{who} — {p['titre_fr'][:80]}")
        extrait = p.get("extrait") or ""
        if extrait:
            print(f"  extrait ({len(extrait)} car.) : {extrait[:300]}…")
        else:
            print("  (aucun extrait de transcript aligné sur cette plage)")
