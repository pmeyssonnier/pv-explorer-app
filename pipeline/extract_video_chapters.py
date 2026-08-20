# ══════════════════════════════════════════════════════════════════════════
#  EXTRACTION DU CHAPITRAGE DES CONSEILS COMMUNAUX (YouTube @1030be) → JSON
#
#  Couche « ordre du jour filmé » : pour chaque séance filmée et chapitrée,
#  la liste des points débattus avec leur type, leur auteur et un DEEP-LINK
#  vers l'instant exact de la vidéo (…&t=SECONDESs).
#
#  Complémentaire aux PV / délibérations : capte les motions, questions et
#  demandes (souvent absentes des PV) et permet un lien « ▶ voir le débat ».
#
#  SOURCE   : chaîne de communication de la commune (vidéos « Conseil communal
#             du JJ/MM/AAAA … »). Le chapitrage vient soit des chapitres
#             officiels YouTube, soit — à défaut — des timestamps de la
#             description (repli).
#
#  À LANCER dans Google Colab (YouTube est joignable depuis Colab, contrairement
#  à 1030.be qui bloque les IP cloud). Dépendances :
#      pip install -U yt-dlp
#      # + un runtime JS pour yt-dlp (recommandé) :
#      curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh
#
#  LIMITES connues : seules les séances récentes sont chapitrées ; les vidéos
#  plus anciennes n'ont pas d'horodatage (0 point). Il n'y a PAS de sous-titres
#  sur ces vidéos → la transcription des débats (couche 2) nécessiterait un ASR
#  (Whisper), non couvert ici.
# ══════════════════════════════════════════════════════════════════════════
import json
import re
import time

import yt_dlp

CHANNEL = "https://www.youtube.com/@1030be/videos"
OUT_PATH = "/content/pv_video_conseil_schaerbeek.json"
MAX_VIDEOS = None          # None = toutes les séances ; un entier pour un test

# Indices « néerlandais » : servent à couper la moitié NL du titre bilingue.
NL_START = re.compile(
    r"^(de |het |verzoek|motie|vraag|beheers|belasting|aanslag|vzw|rekeningen|"
    r"goedkeuring|overeenkomst|mobiliteit|schaarbeek|oproep|e situatie|in afkorting|"
    r"reglement|overheidsopdracht|duurzaam|handvest|erfpacht|bezetting|organisatie|"
    r"wijziging|nieuw huishoudelijk|gebouw|herlancering|geheim|stemming)", re.I)

# Points procéduraux à IGNORER (aucune valeur de recherche ; deep-link inutile).
NOISE = re.compile(
    r"^(d[ée]but|begin|vote|stemming|geheim|comit[ée]\s+secret|fin\b|einde|"
    r"mise à l|huldiging)", re.I)

# Timestamps d'une description : « HH:MM:SS Titre » ou « MM:SS Titre ».
TS_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s+(.+?)\s*$")

# Auteur — format formel FR/NL, puis replis (parenthèses / nom en fin de titre).
# Le mot-clé du repli est en casse-insensible SCOPÉE (?i:…) pour ne pas rendre
# insensible l'exigence de majuscule sur le nom lui-même.
AUTHOR_FORMAL = re.compile(
    r"(?:Demande|Motion|Motie|Question|Vraag|Interpellation|Verzoek)\s+d[eu]\s+"
    r"(?:Monsieur|Madame|M\.|Mme|de heer|Mevrouw)\s+([^\-\–\)\n]+?)(?=\s[-–]\s|\)|$)",
    re.I)
AUTHOR_PAREN = re.compile(
    r"\(\s*(?:(?i:motie|motion|question|vraag|demande|verzoek)\s+)?"
    r"([A-ZÀ-Ý][\wÀ-ÿ'’.-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'’.-]+){0,2})\s*\)\s*$")
AUTHOR_TAIL = re.compile(
    r"[-–·]\s*(?:(?i:motion|motie)\s+)?"
    r"([A-ZÀ-Ý][\wÀ-ÿ'’.-]+\s+[A-ZÀ-Ý][\wÀ-ÿ'’.-]+)\s*$")


def iso_date(title):
    """« Conseil communal du 11/02/2026 … » → « 2026-02-11 » (ou None)."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", title or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def fr_part(title):
    """Isole la moitié FR : s'arrête au 1er segment qui « sonne » néerlandais."""
    fr = []
    for seg in re.split(r"\s[–-]\s", title):
        if NL_START.match(seg.strip()):
            break
        fr.append(seg)
    return " - ".join(fr).strip() or title


def point_type(t):
    """Type du point à partir de mots-clés FR/NL (bornes de mot pour « motion »)."""
    tl = t.lower()
    if re.search(r"\bmoti(on|e)\b", tl):
        return "motion"
    if "question de" in tl or "vraag van" in tl:
        return "question"
    if "demande de" in tl or "verzoek van" in tl:
        return "demande"
    if "interpellation" in tl:
        return "interpellation"
    if "prise d" in tl or "akte nemen" in tl:
        return "prise_acte"
    if "approbation" in tl or "goedkeuring" in tl:
        return "approbation"
    if "taxe" in tl or "belasting" in tl:
        return "taxe"
    if "règlement" in tl or "reglement" in tl:
        return "reglement"
    return "point"


def author(t):
    """Nom de l'auteur du point (élu·e / habitant·e), ou None."""
    for rx in (AUTHOR_FORMAL, AUTHOR_PAREN, AUTHOR_TAIL):
        m = rx.search(t)
        if m:
            return m.group(1).strip()
    return None


def mk_point(title, start, vid):
    start = int(start)
    return {
        "titre": title,
        "titre_fr": fr_part(title),
        "type": point_type(title),
        "auteur": author(title),
        "start_s": start,
        "deeplink": f"https://www.youtube.com/watch?v={vid}&t={start}s",
    }


def points_from_chapters(v, vid):
    """Points issus des chapitres officiels YouTube (hors bruit procédural)."""
    out = []
    for ch in (v.get("chapters") or []):
        t = (ch.get("title") or "").strip()
        if not t or "untitled" in t.lower() or NOISE.match(t):
            continue
        out.append(mk_point(t, ch.get("start_time") or 0, vid))
    return out


def points_from_description(v, vid):
    """Repli : timestamps dans la description quand il n'y a pas de chapitres."""
    out = []
    for line in (v.get("description") or "").splitlines():
        m = TS_RE.match(line)
        if not m:
            continue
        h, mn, se = m.group(1), m.group(2), m.group(3)
        start = int(h) * 3600 + int(mn) * 60 + int(se) if se else int(h) * 60 + int(mn)
        t = m.group(4).strip()
        if not t or "untitled" in t.lower() or NOISE.match(t):
            continue
        out.append(mk_point(t, start, vid))
    return out


def list_council_videos():
    """Vidéos de la chaîne dont le titre est « Conseil communal du … », datées."""
    opts = {"quiet": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        listing = ydl.extract_info(CHANNEL, download=False)
    council = []
    for e in listing.get("entries", []):
        title = e.get("title") or ""
        if "conseil communal du" in title.lower() and iso_date(title):
            council.append({"id": e["id"], "title": title, "date": iso_date(title)})
    council.sort(key=lambda x: x["date"], reverse=True)   # récent d'abord
    return council[:MAX_VIDEOS] if MAX_VIDEOS else council


def main():
    council = list_council_videos()
    print(f"Séances de conseil : {len(council)}")

    seances = []
    for i, c in enumerate(council, 1):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
                v = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={c['id']}", download=False)
        except Exception as ex:                      # bot-check ponctuel, vidéo privée…
            print(f"  ⚠ {c['date']} : échec ({str(ex)[:70]})")
            continue

        points = points_from_chapters(v, c["id"])
        method = "chapitres"
        if not points:
            points = points_from_description(v, c["id"])
            method = "description"

        seances.append({
            "date": c["date"],
            "video_id": c["id"],
            "video_url": f"https://www.youtube.com/watch?v={c['id']}",
            "titre_video": v.get("title"),
            "duree_s": v.get("duration"),
            "points": points,
        })
        print(f"  {i:>3}/{len(council)}  {c['date']}  →  {len(points):>3} points ({method})")
        time.sleep(0.4)                              # politesse

    db = {"source": "video_conseil_schaerbeek", "channel": "@1030be", "seances": seances}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    tot = sum(len(s["points"]) for s in seances)
    avec = sum(1 for s in seances if s["points"])
    print(f"\n✅ {len(seances)} séances ({avec} avec ordre du jour) · {tot} points → {OUT_PATH}")

    for s in seances:                                # aperçu : 1re séance non vide
        if s["points"]:
            print(f"\nExemple — {s['date']} ({s['titre_video']}) :")
            for p in s["points"][:8]:
                who = f" · {p['auteur']}" if p["auteur"] else ""
                print(f"  {p['start_s'] // 60:>3}min  [{p['type']}]{who}  {p['titre_fr'][:80]}")
            break


if __name__ == "__main__":
    main()
