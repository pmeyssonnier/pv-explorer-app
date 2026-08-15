"""
╔══════════════════════════════════════════════════════════════════════════╗
║  PIPELINE EXTRACTION PV CONSEIL COMMUNAL SCHAERBEEK → JSON              ║
║  Google Colab - Python 3.10+                          VERSION 2.1        ║
╚══════════════════════════════════════════════════════════════════════════╝

CHANGELOG v2.1 (corrections code review Opus 4.8) :
  - FIX #1 : client API instancié dans run_pipeline() (plus au niveau module)
  - FIX #2 : logger initialisé paresseusement (plus de crash à l'import)
  - FIX #3 : variable `raw` toujours définie avant le bloc except
  - FIX #4 : shutil importé en tête de fichier
  - FIX #5 : cache basé sur SHA-256 (plus MD5)
  - FIX #6 : commentaire modèle corrigé (claude-haiku-4-5)
  - FIX #7 : enrich_seance_meta extrait les noms depuis le regex (plus de hardcode)
  - DESIGN : rotation des backups + écriture atomique

COÛT (10 500 pages) : Haiku ≈ $42 | Sonnet ≈ $157
"""

import os
import re
import json
import time
import shutil          # FIX #4
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import pdfplumber
import anthropic
from tqdm import tqdm

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════
CONFIG = {
    "DRIVE_ROOT":   "/content/drive/MyDrive/PV_Schaerbeek",
    "INPUT_DIR":    "/content/drive/MyDrive/PV_Schaerbeek/input",
    "OUTPUT_DIR":   "/content/drive/MyDrive/PV_Schaerbeek/output",
    "CACHE_DIR":    "/content/drive/MyDrive/PV_Schaerbeek/cache",
    "BACKUP_DIR":   "/content/drive/MyDrive/PV_Schaerbeek/backups",
    "LOG_FILE":     "/content/drive/MyDrive/PV_Schaerbeek/pipeline.log",
    "DB_JSON_PATH": "/content/drive/MyDrive/PV_Schaerbeek/pv_conseil_schaerbeek.json",
    "PROGRESS_FILE": "/content/drive/MyDrive/PV_Schaerbeek/progress.json",

    # Laisser "" pour lire depuis la variable d'environnement ANTHROPIC_API_KEY
    "ANTHROPIC_API_KEY": "",
    "MODEL":      "claude-haiku-4-5-20251001",   # FIX #6 : nom correct
    "MAX_TOKENS": 4096,

    "CHUNK_SIZE":         12,
    "API_DELAY_SEC":      1.5,
    "SKIP_ALREADY_DONE":  True,
    "MAX_RETRIES":        3,
    "MAX_BACKUPS":        10,     # DESIGN : rotation
}

# ══════════════════════════════════════════════════════════════════════════
# LOGGER (FIX #2 : init paresseuse — plus de crash à l'import)
# ══════════════════════════════════════════════════════════════════════════
_logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("pv_pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    log_path = Path(CONFIG["LOG_FILE"])
    if log_path.parent.exists():
        try:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError:
            pass
    _logger = logger
    return logger


def _attach_file_logging():
    logger = get_logger()
    log_path = Path(CONFIG["LOG_FILE"])
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers) and log_path.parent.exists():
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)

# ══════════════════════════════════════════════════════════════════════════
# CLIENT API (FIX #1 : instancié paresseusement)
# ══════════════════════════════════════════════════════════════════════════
_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is not None:
        return _client
    api_key = CONFIG.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("sk-ant-..."):
        raise ValueError(
            "Clé API manquante. Renseigne CONFIG['ANTHROPIC_API_KEY'] "
            "ou définis la variable d'environnement ANTHROPIC_API_KEY."
        )
    _client = anthropic.Anthropic(api_key=api_key)
    return _client

# ══════════════════════════════════════════════════════════════════════════
# EXTRACTION TEXTE PDF
# ══════════════════════════════════════════════════════════════════════════
def extract_pdf_metadata(pdf_path: Path) -> dict:
    name = pdf_path.stem
    meta = {"filename": pdf_path.name, "filepath": str(pdf_path), "date": None, "seance_id": None}
    for pattern in [r"(\d{4})[._\-](\d{2})[._\-](\d{2})", r"(\d{4})(\d{2})(\d{2})"]:
        m = re.search(pattern, name)
        if m:
            y, mo, d = m.groups()
            meta["date"] = f"{y}-{mo}-{d}"
            meta["seance_id"] = f"PV-{y}-{mo}-{d}"
            break
    return meta


def extract_text_from_pdf(pdf_path: Path) -> list[dict]:
    log = get_logger()
    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = (page.extract_text() or "").strip()
                pages.append({"page_num": i + 1, "text": text, "char_count": len(text)})
        log.info(f"  PDF extrait : {len(pages)} pages — {sum(p['char_count'] for p in pages):,} chars")
    except Exception as e:
        log.error(f"  Erreur extraction {pdf_path.name}: {e}")
    return pages


def chunk_pages(pages: list[dict], chunk_size: int) -> list[list[dict]]:
    return [pages[i:i + chunk_size] for i in range(0, len(pages), chunk_size)]

# ══════════════════════════════════════════════════════════════════════════
# PROMPT SYSTEM
# ══════════════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """Tu es un expert en analyse de procès-verbaux du Conseil Communal belge (Schaerbeek).
Tu dois extraire les points de l'ordre du jour à partir du texte brut de pages de PV et les structurer en JSON.

SCHÉMA JSON D'UN POINT :
{
  "sp": <int>, "urgence": <bool>,
  "type": <"point_normal"|"point_urgent"|"motion"|"demande_habitant"|"question_orale">,
  "rubrique": <string>, "sous_rubrique": <string>,
  "titre": <string>, "resume": <string>,
  "intervenants": <array>, "repondant": <string|null>,
  "decision": <"DÉCIDE"|"PREND ACTE"|"APPROUVÉ"|"PREND POUR INFORMATION"|"REPORTÉ"|"DÉBAT"|"MINUTE DE SILENCE">,
  "vote": {"type": <"unanimite"|"vote_nominal"|"reporte"|null>, "pour": <int|null>, "contre": <int>, "abstentions": <int>},
  "montant_eur": <float|null>, "thematiques": <array snake_case>
}

RÈGLES :
- "Approuvé à l'unanimité" → vote.type="unanimite"
- "Décidé par X voix contre Y et Z abstentions" → vote.type="vote_nominal"
- "Ce point est reporté" → vote.type="reporte", decision="REPORTÉ"
- SP > 67 souvent urgences ; motions = "Motion de..." ; habitants = "Demande de..."
- Extrais montants même dans considérants (ex: "65.000 € TVAC")
- IGNORE le texte néerlandais (version NL) pour éviter doublons

RÉPONDS UNIQUEMENT en JSON valide :
{"date_seance": <"YYYY-MM-DD" ou null>, "points": [...]}
Pas de markdown, uniquement le JSON brut.
"""


def make_user_prompt(pages_chunk: list[dict], date_hint: Optional[str] = None) -> str:
    date_str = f"\nDate de séance connue : {date_hint}" if date_hint else ""
    pages_text = "\n\n---PAGE---\n\n".join(
        f"[Page {p['page_num']}]\n{p['text']}" for p in pages_chunk if p["char_count"] > 50
    )
    return f"Extrais tous les points SP présents dans ces pages de PV.{date_str}\n\nTEXTE :\n{pages_text}\n\nRetourne le JSON structuré."

# ══════════════════════════════════════════════════════════════════════════
# CACHE (FIX #5 : SHA-256)
# ══════════════════════════════════════════════════════════════════════════
def get_cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def load_from_cache(cache_key: str) -> Optional[dict]:
    cache_file = Path(CONFIG["CACHE_DIR"]) / f"{cache_key}.json"
    if cache_file.exists():
        try:
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_to_cache(cache_key: str, data: dict):
    Path(CONFIG["CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
    with open(Path(CONFIG["CACHE_DIR"]) / f"{cache_key}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ══════════════════════════════════════════════════════════════════════════
# APPEL API (FIX #3 : `raw` toujours défini)
# ══════════════════════════════════════════════════════════════════════════
def call_claude_api(user_prompt: str) -> Optional[dict]:
    log = get_logger()
    cache_key = get_cache_key(user_prompt)
    cached = load_from_cache(cache_key)
    if cached is not None:
        log.debug(f"    Cache hit : {cache_key}")
        return cached

    client = get_client()
    for attempt in range(CONFIG["MAX_RETRIES"]):
        raw = ""   # FIX #3 : défini avant tout usage dans les except
        try:
            response = client.messages.create(
                model=CONFIG["MODEL"], max_tokens=CONFIG["MAX_TOKENS"],
                system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user_prompt}]
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
            data = json.loads(raw)
            save_to_cache(cache_key, data)
            time.sleep(CONFIG["API_DELAY_SEC"])
            return data
        except json.JSONDecodeError as e:
            log.warning(f"    JSON invalide (tentative {attempt+1}): {e}")
            log.debug(f"    Réponse brute (200c): {raw[:200]}")
            time.sleep(2 ** attempt)
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            log.warning(f"    Rate limit — attente {wait}s")
            time.sleep(wait)
        except anthropic.APIError as e:
            log.error(f"    Erreur API (tentative {attempt+1}): {e}")
            time.sleep(5)
        except Exception as e:
            log.error(f"    Erreur inattendue (tentative {attempt+1}): {e}")
            time.sleep(5)
    log.error(f"    Échec après {CONFIG['MAX_RETRIES']} tentatives")
    return None

# ══════════════════════════════════════════════════════════════════════════
# TRAITEMENT PDF
# ══════════════════════════════════════════════════════════════════════════
def process_pdf(pdf_path: Path) -> Optional[dict]:
    log = get_logger()
    log.info(f"\n{'='*60}")
    log.info(f"Traitement : {pdf_path.name}")
    meta = extract_pdf_metadata(pdf_path)
    log.info(f"  Date inférée : {meta['date']}")

    pages = extract_text_from_pdf(pdf_path)
    if not pages:
        log.error("  Aucune page extraite — PDF peut-être scanné/image")
        return None
    pages = [p for p in pages if p["char_count"] > 100]
    log.info(f"  Pages utiles : {len(pages)}")

    chunks = chunk_pages(pages, CONFIG["CHUNK_SIZE"])
    log.info(f"  Chunks : {len(chunks)} × {CONFIG['CHUNK_SIZE']} pages")

    all_points = []
    seance_date = meta["date"]
    for i, chunk in enumerate(chunks):
        log.info(f"  Chunk {i+1}/{len(chunks)} (pages {chunk[0]['page_num']}-{chunk[-1]['page_num']})")
        result = call_claude_api(make_user_prompt(chunk, seance_date))
        if result is None:
            log.warning(f"    Chunk {i+1} ignoré (échec API)")
            continue
        if result.get("date_seance") and not seance_date:
            seance_date = result["date_seance"]
        points = result.get("points", [])
        log.info(f"    → {len(points)} points extraits")
        all_points.extend(points)

    if not all_points:
        log.warning("  Aucun point extrait")
        return None

    sp_map = {}
    for p in all_points:
        sp = p.get("sp")
        if sp is None:
            continue
        if sp not in sp_map or sum(1 for v in p.values() if v) > sum(1 for v in sp_map[sp].values() if v):
            sp_map[sp] = p
    deduped = sorted(sp_map.values(), key=lambda p: p.get("sp", 999))
    log.info(f"  Points dédupliqués : {len(all_points)} → {len(deduped)}")

    seance_struct = {
        "seance": {
            "id": meta["seance_id"] or f"PV-{seance_date}", "date": seance_date,
            "source_file": meta["filename"], "extracted_at": datetime.now().isoformat(),
            "heure_ouverture": None, "heure_cloture": None, "president": None,
            "bourgmestre": None, "bourgmestre_ff": None, "secretaire_communal": None,
            "presents_count": None, "excuses": [], "absents": [],
        },
        "points": deduped
    }
    enrich_seance_meta(seance_struct, pages[:2])
    return seance_struct


def _clean_name(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip(" :-\t")


def enrich_seance_meta(seance_struct: dict, first_pages: list[dict]):
    """FIX #7 : extrait les noms depuis le texte, ne les hardcode plus."""
    text = " ".join(p["text"] for p in first_pages)
    s = seance_struct["seance"]

    m = re.search(r"ouvre\s+en\s+séance\s+publique\s+à\s+(\d{1,2})h(\d{0,2})", text, re.IGNORECASE)
    if m:
        s["heure_ouverture"] = f"{m.group(1)}:{m.group(2) or '00'}"
    m = re.search(r"séance\s+(?:publique\s+)?est\s+levée\s+à\s+(\d{1,2})h(\d{0,2})", text, re.IGNORECASE)
    if m:
        s["heure_cloture"] = f"{m.group(1)}:{m.group(2) or '00'}"

    NAME = r"([A-ZÀ-Ý][\wÀ-ÿ'\-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'\-]+)*)"
    m = re.search(NAME + r"\s*,?\s*Président\s+du\s+[Cc]onseil", text)
    if m:
        s["president"] = _clean_name(m.group(1))
    m = re.search(NAME + r"\s*,?\s*Bourgmestre\s+ff", text, re.IGNORECASE)
    if m:
        s["bourgmestre_ff"] = _clean_name(m.group(1))
    else:
        m = re.search(NAME + r"\s*,?\s*Bourgmestre\b", text)
        if m:
            s["bourgmestre"] = _clean_name(m.group(1))
    m = re.search(NAME + r"\s*,?\s*Secrétaire\s+[Cc]ommunal\b", text)
    if m:
        s["secretaire_communal"] = _clean_name(m.group(1))

    presents = re.search(r"PRÉSENTS[^:]*:(.*?)(?:ABSENTS|EXCUSÉS|EN DÉBUT)", text, re.DOTALL | re.IGNORECASE)
    if presents:
        count = len(re.findall(r"M\.\-h\.|Mme\-mevr\.|MM\.\-hh\.|Mmes\-mevr\.", presents.group(1)))
        if count:
            s["presents_count"] = count

# ══════════════════════════════════════════════════════════════════════════
# FUSION BASE JSON
# ══════════════════════════════════════════════════════════════════════════
def load_database() -> dict:
    log = get_logger()
    db_path = Path(CONFIG["DB_JSON_PATH"])
    if db_path.exists():
        with open(db_path, encoding="utf-8") as f:
            db = json.load(f)
        log.info(f"Base existante chargée : {len(db.get('seances', []))} séances")
        return db
    log.info("Nouvelle base créée")
    return {"meta": {"nom": "Base PV Conseil Communal Schaerbeek", "version": "2.1",
                     "date_creation": datetime.now().isoformat(), "seances_incluses": [],
                     "total_points": 0}, "seances": []}


def _rotate_backups():
    """DESIGN : garde seulement les MAX_BACKUPS backups récents."""
    backup_dir = Path(CONFIG["BACKUP_DIR"])
    if not backup_dir.exists():
        return
    backups = sorted(backup_dir.glob("pv_conseil_schaerbeek.backup_*.json"))
    for old in backups[:max(0, len(backups) - CONFIG["MAX_BACKUPS"])]:
        try:
            old.unlink()
        except OSError:
            pass


def save_database(db: dict):
    log = get_logger()
    db_path = Path(CONFIG["DB_JSON_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        backup_dir = Path(CONFIG["BACKUP_DIR"])
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(db_path, backup_dir / f"pv_conseil_schaerbeek.backup_{stamp}.json")
        _rotate_backups()

    db["meta"]["seances_incluses"] = [s["seance"]["date"] for s in db["seances"] if s["seance"]["date"]]
    db["meta"]["total_points"] = sum(len(s["points"]) for s in db["seances"])
    db["meta"]["last_updated"] = datetime.now().isoformat()

    # Écriture atomique (évite corruption si crash pendant l'écriture)
    tmp = db_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    tmp.replace(db_path)
    log.info(f"Base sauvegardée : {db['meta']['total_points']} points, {len(db['seances'])} séances")


def merge_seance_into_db(db: dict, new_seance: dict) -> bool:
    log = get_logger()
    new_date = new_seance["seance"]["date"]
    if not new_date:
        log.warning("  Séance sans date — ignorée")
        return False
    existing = next((s for s in db["seances"] if s["seance"]["date"] == new_date), None)
    if existing:
        existing_sps = {p.get("sp") for p in existing["points"]}
        added = updated = 0
        for new_point in new_seance["points"]:
            sp = new_point.get("sp")
            if sp not in existing_sps:
                existing["points"].append(new_point)
                added += 1
            else:
                idx = next(i for i, p in enumerate(existing["points"]) if p.get("sp") == sp)
                if sum(1 for v in new_point.values() if v) > sum(1 for v in existing["points"][idx].values() if v):
                    existing["points"][idx] = new_point
                    updated += 1
        existing["points"].sort(key=lambda p: p.get("sp", 999))
        for k, v in new_seance["seance"].items():
            if v and not existing["seance"].get(k):
                existing["seance"][k] = v
        log.info(f"  Séance {new_date} mise à jour : +{added} ajoutés, {updated} enrichis")
    else:
        db["seances"].append(new_seance)
        db["seances"].sort(key=lambda s: s["seance"].get("date") or "")
        log.info(f"  Séance {new_date} ajoutée ({len(new_seance['points'])} points)")
    return True

# ══════════════════════════════════════════════════════════════════════════
# PROGRESSION
# ══════════════════════════════════════════════════════════════════════════
def load_progress() -> set:
    pf = Path(CONFIG["PROGRESS_FILE"])
    if pf.exists():
        try:
            with open(pf) as f:
                return set(json.load(f).get("done", []))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_progress(done: set):
    pf = Path(CONFIG["PROGRESS_FILE"])
    pf.parent.mkdir(parents=True, exist_ok=True)
    tmp = pf.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump({"done": sorted(done), "last_updated": datetime.now().isoformat()}, f, indent=2)
    tmp.replace(pf)


def get_pdf_list(input_dir: str) -> list[Path]:
    log = get_logger()
    pdfs = sorted(Path(input_dir).glob("**/*.pdf"))
    log.info(f"PDF trouvés : {len(pdfs)} fichiers dans {input_dir}")
    return pdfs

# ══════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════
def run_pipeline(max_files: Optional[int] = None, dry_run: bool = False,
                 force_reprocess: bool = False) -> dict:
    for d in [CONFIG["DRIVE_ROOT"], CONFIG["OUTPUT_DIR"], CONFIG["CACHE_DIR"], CONFIG["BACKUP_DIR"]]:
        Path(d).mkdir(parents=True, exist_ok=True)
    _attach_file_logging()

    log = get_logger()
    log.info("\n" + "█"*60)
    log.info("DÉMARRAGE PIPELINE PV EXTRACTION v2.1")
    log.info(f"Modèle : {CONFIG['MODEL']} | Chunk : {CONFIG['CHUNK_SIZE']} pages")
    log.info("█"*60)

    if not dry_run:
        try:
            get_client()
        except ValueError as e:
            log.error(str(e))
            return {}

    done = load_progress() if (CONFIG["SKIP_ALREADY_DONE"] and not force_reprocess) else set()
    db = load_database()
    pdfs = get_pdf_list(CONFIG["INPUT_DIR"])
    if max_files:
        pdfs = pdfs[:max_files]

    stats = {"total": len(pdfs), "done": 0, "skipped": 0, "failed": 0, "points": 0}
    for pdf_path in tqdm(pdfs, desc="PDFs traités", unit="fichier"):
        fname = pdf_path.name
        if fname in done:
            log.info(f"⏭  Déjà traité : {fname}")
            stats["skipped"] += 1
            continue
        if dry_run:
            pages = extract_text_from_pdf(pdf_path)
            log.info(f"[DRY RUN] {fname} : {len(pages)} pages")
            stats["done"] += 1
            continue
        seance = process_pdf(pdf_path)
        if seance is None:
            stats["failed"] += 1
            (Path(CONFIG["OUTPUT_DIR"]) / f"FAILED_{fname}.txt").write_text(
                f"Échec : {fname}\n{datetime.now().isoformat()}")
            continue
        seance_out = Path(CONFIG["OUTPUT_DIR"]) / f"{seance['seance']['id']}.json"
        with open(seance_out, "w", encoding="utf-8") as f:
            json.dump(seance, f, ensure_ascii=False, indent=2)
        log.info(f"  Séance sauvegardée : {seance_out.name}")
        merge_seance_into_db(db, seance)
        stats["points"] += len(seance["points"])
        save_database(db)
        done.add(fname)
        save_progress(done)
        stats["done"] += 1

    log.info("\n" + "═"*60)
    log.info("RAPPORT FINAL")
    log.info(f"  Total: {stats['total']} | Traités: {stats['done']} | Ignorés: {stats['skipped']} | Échoués: {stats['failed']}")
    log.info(f"  Points extraits : {stats['points']}")
    log.info(f"  Base finale : {db['meta']['total_points']} points / {len(db['seances'])} séances")
    log.info("═"*60)
    return db

# ══════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════
def validate_database(db: dict) -> dict:
    log = get_logger()
    issues = []
    for seance in db["seances"]:
        date = seance["seance"]["date"]
        seen = set()
        for p in seance["points"]:
            sp = p.get("sp")
            if sp in seen:
                issues.append(f"{date}: SP {sp} dupliqué")
            seen.add(sp)
            if not p.get("titre"):
                issues.append(f"{date} SP{p.get('sp','?')}: titre manquant")
            m = p.get("montant_eur")
            if m and (m < 0 or m > 100_000_000):
                issues.append(f"{date} SP{p.get('sp','?')}: montant suspect {m}")
    log.info(f"\nValidation : {len(issues)} problèmes")
    for issue in issues[:20]:
        log.warning(f"  ⚠ {issue}")
    if len(issues) > 20:
        log.warning(f"  ... et {len(issues)-20} autres")
    return {"total_issues": len(issues), "issues": issues}


def export_csv(db: dict, output_path: str):
    import csv
    log = get_logger()
    rows = []
    for seance in db["seances"]:
        date = seance["seance"]["date"]
        for p in seance["points"]:
            vote = p.get("vote", {}) or {}
            rows.append({
                "date": date, "sp": p.get("sp"), "type": p.get("type"),
                "rubrique": p.get("rubrique"), "sous_rubrique": p.get("sous_rubrique"),
                "titre": (p.get("titre") or "")[:200], "resume": (p.get("resume") or "")[:300],
                "decision": p.get("decision"), "vote_type": vote.get("type"),
                "vote_pour": vote.get("pour"), "vote_contre": vote.get("contre"),
                "vote_abstentions": vote.get("abstentions"), "montant_eur": p.get("montant_eur"),
                "urgence": p.get("urgence"),
                "intervenants": "; ".join(p.get("intervenants") or []),
                "repondant": p.get("repondant"),
                "thematiques": ", ".join(p.get("thematiques") or []),
            })
    if not rows:
        log.warning("Base vide — pas de CSV")
        return
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    log.info(f"CSV exporté : {output_path} ({len(rows)} lignes)")


def stats_summary(db: dict):
    from collections import Counter
    all_points = [p for s in db["seances"] for p in s["points"]]
    print(f"\n{'━'*50}\n  STATISTIQUES BASE PV SCHAERBEEK\n{'━'*50}")
    print(f"  Séances: {len(db['seances'])} | Points: {len(all_points)}")
    print("\n  Par type :")
    for t, c in Counter(p.get("type") for p in all_points).most_common():
        print(f"    {str(t):25s} : {c}")
    print("\n  Par rubrique (top 10) :")
    for r, c in Counter(p.get("rubrique") for p in all_points).most_common(10):
        print(f"    {str(r):30s} : {c}")
    montants = [p["montant_eur"] for p in all_points if p.get("montant_eur")]
    if montants:
        print(f"\n  Montants : total {sum(montants):,.0f}€ | max {max(montants):,.0f}€ | {len(montants)} mentions")
    print(f"{'━'*50}\n")


# UTILISATION COLAB :
#   run_pipeline(max_files=2, dry_run=True)   # test sans API
#   db = run_pipeline()                        # production
#   validate_database(db); stats_summary(db)
#   export_csv(db, "/content/drive/MyDrive/PV_Schaerbeek/pv_all_points.csv")

if __name__ == "__main__":
    db = run_pipeline(max_files=5)
    stats_summary(db)
