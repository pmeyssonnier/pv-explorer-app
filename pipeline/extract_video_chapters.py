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
#  ANTI-BOT-CHECK (« Sign in to confirm you're not a bot ») : les IP cloud
#  (Colab) déclenchent parfois ce mur sur le client YouTube "web" par défaut.
#  PLAYER_CLIENTS force des clients alternatifs (android/tv/web_safari) qui le
#  contournent le plus souvent ; RETRIES relance automatiquement une vidéo qui
#  échoue (le mur est intermittent, pas systématique).
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

CHANNEL_BASE = "https://www.youtube.com/@1030be"
# /streams = livestreams archivés (les séances 2025+ n'y sont QUE là, pas /videos).
TABS = ("/videos", "/streams")
OUT_PATH = "/content/pv_video_conseil_schaerbeek.json"
SESSIONS_PATH = "/content/video_sessions.json"   # date → URL vidéo (à committer dans backend/)
MAX_VIDEOS = None          # None = toutes les séances ; un entier pour un test

# Clients YouTube à essayer pour l'extraction par vidéo (dans cet ordre) : le
# client "web" par défaut est celui qui déclenche le plus le mur anti-bot sur
# IP cloud ; android/tv/web_safari le contournent le plus souvent.
# ⚠️ yt-dlp attend une LISTE de noms (pas une chaîne unique avec virgules :
# passée telle quelle elle est vue comme UN SEUL nom de client, invalide →
# "Skipping unsupported client" et repli silencieux sur le client par défaut).
PLAYER_CLIENTS = ["android", "tv", "web_safari", "web"]
RETRIES = 3          # tentatives par vidéo avant d'abandonner
RETRY_WAIT_S = 5      # pause entre tentatives (secondes)

# Indices « néerlandais » : servent à couper la moitié NL du titre bilingue.
NL_START = re.compile(
    r"^(de |het |verzoek|motie|vraag|beheers|belasting|aanslag|vzw|rekeningen|"
    r"goedkeuring|overeenkomst|mobiliteit|schaarbeek|oproep|e situatie|in afkorting|"
    r"reglement|overheidsopdracht|duurzaam|handvest|erfpacht|bezetting|organisatie|"
    r"wijziging|nieuw huishoudelijk|gebouw|herlancering|geheim|stemming)", re.I)

# Points procéduraux à IGNORER (aucune valeur de recherche ; deep-link inutile).
NOISE = re.compile(
    r"^(d[ée]but|begin|vote|stemming|geheim|comit[ée]\s+secret|fin\b|einde|"
    r"mise à l|huldiging|abonnez|suivez|r[ée][ée]cout|facebook|instagram|newsletter)",
    re.I)

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
    """« … du 15/10/2025 … » ou « … 15/10/25 » → « 2025-10-15 » (ou None).
    Gère l'année sur 2 ou 4 chiffres (les livestreams utilisent parfois 25)."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{2,4})", title or "")
    if not m:
        return None
    d, mo, y = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:
        y = "20" + y
    return f"{y}-{mo}-{d}"


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
    """Séances « Conseil communal / Gemeenteraad … » datées, sur /videos ET
    /streams (les livestreams archivés — dont 2025+ — ne sont que dans /streams).
    Filtre élargi (« conseil communal » OU « gemeenteraad ») + garde-fou date."""
    opts = {"quiet": True, "extract_flat": True, "skip_download": True}
    seen, council = set(), []
    for tab in TABS:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                listing = ydl.extract_info(CHANNEL_BASE + tab, download=False)
        except Exception as ex:
            print(f"  ⚠ onglet {tab} ignoré ({str(ex)[:60]})")
            continue
        for e in listing.get("entries", []):
            vid = e.get("id")
            title = e.get("title") or ""
            tl = title.lower()
            if not vid or vid in seen:
                continue
            if ("conseil communal" in tl or "gemeenteraad" in tl) and iso_date(title):
                seen.add(vid)
                council.append({"id": vid, "title": title, "date": iso_date(title)})
    council.sort(key=lambda x: x["date"], reverse=True)   # récent d'abord
    return council[:MAX_VIDEOS] if MAX_VIDEOS else council


def main():
    council = list_council_videos()
    print(f"Séances de conseil : {len(council)}")

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extractor_args": {"youtube": {"player_client": PLAYER_CLIENTS}},
    }

    seances = []
    for i, c in enumerate(council, 1):
        v = None
        last_err = None
        for attempt in range(1, RETRIES + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    v = ydl.extract_info(
                        f"https://www.youtube.com/watch?v={c['id']}", download=False)
                break
            except Exception as ex:                  # bot-check ponctuel, vidéo privée…
                last_err = ex
                if attempt < RETRIES:
                    time.sleep(RETRY_WAIT_S)
        if v is None:
            print(f"  ⚠ {c['date']} : échec après {RETRIES} tentatives ({str(last_err)[:70]})")
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

    # Carte date → URL de la vidéo de séance (TOUTES les séances filmées, même non
    # chapitrées) : alimente le lien « ▶ voir la séance » du backend
    # (backend/video_sessions.json). À committer quand de nouvelles séances sont
    # ajoutées.
    sessions = {s["date"]: s["video_url"] for s in seances if s.get("video_url")}
    with open(SESSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(sessions.items(), reverse=True)), f, ensure_ascii=False, indent=2)
    print(f"🎬 {len(sessions)} séances filmées → {SESSIONS_PATH}")

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
