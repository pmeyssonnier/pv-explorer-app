"""
╔══════════════════════════════════════════════════════════════════════════╗
║  PIPELINE EXTRACTION QUESTIONS ÉCRITES — CONSEIL COMMUNAL SCHAERBEEK    ║
║  Google Colab - Python 3.10+                          VERSION 1.0        ║
╚══════════════════════════════════════════════════════════════════════════╝

Une question écrite (adressée par un·e conseiller·ère au Collège en dehors
d'une séance, réponse écrite du Collège — voir 1030.be/fr/questions-ecrites)
est un document COURT (1 à 5 pages), bilingue FR/NL, portant UN SEUL
enregistrement (numéro, date, auteur·e, question, réponse) — contrairement à
un PV (des dizaines de points), pas besoin ici du découpage en chunks ni de
l'audit de complétude par regex de pv_extraction_pipeline.py : un seul appel
Claude par fichier suffit. Les petites fonctions de nettoyage/normalisation
partagées (_clean_str, _coerce_int_or_none) sont réimportées de
pv_extraction_pipeline plutôt que dupliquées — même répertoire pipeline/.

Fonctionnement identique au pipeline PV pour le reste : traitement par lot
depuis un dossier Google Drive (run_pipeline, Colab) OU appel unitaire
process_pdf() réutilisé par le panneau admin (voir
backend/services/questions_ecrites_integration.py) pour l'upload ponctuel.

INSTRUCTIONS COLAB :
  Cellule 1 : !pip install pdfplumber anthropic tqdm --quiet
  Cellule 2 : from google.colab import drive; drive.mount('/content/drive')
  Cellule 3 : Colle ce script et exécute run_pipeline()
"""
import os
import re
import json
import time
import shutil
import logging
from pathlib import Path
from datetime import datetime, date as _date
from typing import Callable, Optional

import pdfplumber
import anthropic
from tqdm import tqdm

from pv_extraction_pipeline import _clean_str, _coerce_int_or_none  # noqa: E402

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════
CONFIG = {
    "DRIVE_ROOT":   "/content/drive/MyDrive/QuestionsEcrites_Schaerbeek",
    "INPUT_DIR":    "/content/drive/MyDrive/QuestionsEcrites_Schaerbeek/input",
    "OUTPUT_DIR":   "/content/drive/MyDrive/QuestionsEcrites_Schaerbeek/output",
    "BACKUP_DIR":   "/content/drive/MyDrive/QuestionsEcrites_Schaerbeek/backups",
    "LOG_FILE":     "/content/drive/MyDrive/QuestionsEcrites_Schaerbeek/pipeline.log",
    "DB_JSON_PATH": "/content/drive/MyDrive/QuestionsEcrites_Schaerbeek/questions_ecrites_schaerbeek.json",
    "PROGRESS_FILE": "/content/drive/MyDrive/QuestionsEcrites_Schaerbeek/progress.json",

    "ANTHROPIC_API_KEY": "",   # vide = lu depuis la variable d'environnement
    "MODEL":      "claude-haiku-4-5-20251001",
    "MAX_TOKENS": 4096,        # un seul Q/R, jamais aussi dense qu'un PV

    "API_DELAY_SEC":      1.0,
    "SKIP_ALREADY_DONE":  True,
    "MAX_RETRIES":        3,
    "MAX_BACKUPS":        10,
}

# ══════════════════════════════════════════════════════════════════════════
# LOGGER
# ══════════════════════════════════════════════════════════════════════════
_logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("qe_pipeline")
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
# CLIENT API
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
def extract_text_from_pdf(pdf_path: Path) -> str:
    """Texte de toutes les pages concaténé — document court, pas de chunking
    nécessaire (contrairement au PV, voir pv_extraction_pipeline.extract_text_from_pdf,
    dont la note sur la fuite mémoire pdfplumber s'applique de la même façon
    ici : page.close() après chaque page, même si le volume est bien moindre)."""
    log = get_logger()
    parts = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                parts.append((page.extract_text() or "").strip())
                page.close()
    except Exception as e:
        log.error(f"  Erreur extraction {pdf_path.name}: {e}")
        return ""
    text = "\n\n".join(p for p in parts if p)
    log.info(f"  PDF extrait : {len(parts)} page(s) — {len(text):,} chars")
    return text

# ══════════════════════════════════════════════════════════════════════════
# PROMPT SYSTEM
# ══════════════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """Tu es un expert en analyse de questions écrites adressées au Collège des \
Bourgmestre et Échevins d'une commune belge (Schaerbeek). Le document est bilingue \
(français/néerlandais) : la version française apparaît en premier, séparée de la version \
néerlandaise par "-=-". N'extrais QUE la version française.

SCHÉMA JSON :
{
  "numero": <int>,             // numéro en tête du document (ex. "015." -> 15, "008." -> 8)
  "date": <"YYYY-MM-DD"|null>, // date de la question (ex. "du 10 novembre 2025" -> "2025-11-10")
  "auteur": <string>,          // nom complet SANS civilité ni fonction (ex. "Georges Verzin",
                                // pas "Monsieur Georges VERZIN, conseiller communal")
  "titre": <string>,           // sujet de la question (le titre en gras/italique sous l'en-tête)
  "question": <string>,        // texte intégral de la question, en français uniquement
  "reponse": <string|null>     // texte intégral de la réponse ("Réponse :"), en français
                                // uniquement ; null si absente du document
}

RÈGLES :
- Ne DEVINE JAMAIS une valeur absente — mets null plutôt qu'une valeur plausible mais fausse.
- N'extrais QUE le texte français ; ignore complètement la version néerlandaise, y compris
  dans le titre bilingue de l'en-tête ("... -=- Vraag van de heer ...").
- "auteur" : nom propre normalisé (Prénom Nom), casse standard, sans civilité/fonction/date.
- Conserve la mise en forme du texte (listes numérotées, paragraphes) sous forme de texte
  brut lisible, pas de markdown.

RÉPONDS UNIQUEMENT en JSON valide, sans markdown, sans texte avant/après :
{"numero": ..., "date": ..., "auteur": ..., "titre": ..., "question": ..., "reponse": ...}
"""


def make_user_prompt(text: str) -> str:
    return f"Extrais les informations de cette question écrite :\n\n{text}\n\nRetourne le JSON structuré."

# ══════════════════════════════════════════════════════════════════════════
# APPEL API
# ══════════════════════════════════════════════════════════════════════════
def call_claude_api(text: str) -> Optional[dict]:
    """Un seul appel (pas de cache ni de découpage en chunks — voir docstring
    du module) : un document trop court/vide pour produire un JSON exploitable
    est un échec définitif, pas un signal pour scinder davantage."""
    log = get_logger()
    client = get_client()
    for attempt in range(CONFIG["MAX_RETRIES"]):
        raw = ""
        try:
            response = client.messages.create(
                model=CONFIG["MODEL"], max_tokens=CONFIG["MAX_TOKENS"],
                system=SYSTEM_PROMPT, messages=[{"role": "user", "content": make_user_prompt(text)}]
            )
            raw = next((b.text for b in response.content
                        if getattr(b, "type", None) == "text"), "").strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
            if not raw.startswith("{"):
                i = raw.find("{")
                if i != -1:
                    raw = raw[i:]
            if not raw.endswith("}"):
                j = raw.rfind("}")
                if j != -1:
                    raw = raw[:j + 1]
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Réponse JSON sans structure attendue")
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
# NORMALISATION
# ══════════════════════════════════════════════════════════════════════════
def _valid_iso_date(s: str) -> Optional[str]:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s or "")
    if not m:
        return None
    try:
        _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return s


def normalize_question(data: dict, filename: str) -> Optional[dict]:
    """Valide la forme minimale exploitable (numéro + date + auteur·e) —
    sans ces 3 champs, l'entrée ne peut pas être identifiée/classée de façon
    fiable, mieux vaut échouer explicitement que publier une entrée orpheline."""
    if not isinstance(data, dict):
        return None
    numero = _coerce_int_or_none(data.get("numero"))
    date = _valid_iso_date(_clean_str(data.get("date")))
    auteur = _clean_str(data.get("auteur"))
    if numero is None or not date or not auteur:
        return None
    annee = int(date[:4])
    return {
        "id": f"QE-{annee}-{numero:03d}",
        "annee": annee,
        "numero": numero,
        "date": date,
        "auteur": auteur,
        "titre": _clean_str(data.get("titre")),
        "question": _clean_str(data.get("question")),
        "reponse": _clean_str(data.get("reponse")) or None,
        "source_file": filename,
        "extracted_at": datetime.now().isoformat(),
    }

# ══════════════════════════════════════════════════════════════════════════
# TRAITEMENT D'UN PDF
# ══════════════════════════════════════════════════════════════════════════
def process_pdf(pdf_path: Path, progress_cb: Optional[Callable[[dict], None]] = None) -> Optional[dict]:
    """`progress_cb`, si fourni, reçoit un petit dict d'avancement à chaque
    étape notable — même convention que pv_extraction_pipeline.process_pdf,
    utilisée par le panneau admin (services/questions_ecrites_integration.py)
    pour afficher une progression réelle. Un seul appel Claude ici (document
    court), donc peu d'étapes."""
    log = get_logger()
    log.info(f"\n{'='*60}")
    log.info(f"Traitement : {pdf_path.name}")

    text = extract_text_from_pdf(pdf_path)
    if not text:
        log.error("  Aucun texte exploitable extrait — PDF peut-être scanné/image")
        return None
    if progress_cb:
        progress_cb({"stage": "extraction"})

    data = call_claude_api(text)
    if progress_cb:
        progress_cb({"stage": "verification"})
    if data is None:
        return None

    question = normalize_question(data, pdf_path.name)
    if question is None:
        log.warning("  Extraction incomplète (numéro/date/auteur·e manquant) — PDF ignoré")
        return None
    log.info(f"  ✅ {question['id']} — {question['auteur']} : {question['titre']}")
    return question

# ══════════════════════════════════════════════════════════════════════════
# FUSION BASE JSON
# ══════════════════════════════════════════════════════════════════════════
def load_database() -> dict:
    log = get_logger()
    db_path = Path(CONFIG["DB_JSON_PATH"])
    if db_path.exists():
        with open(db_path, encoding="utf-8") as f:
            db = json.load(f)
        log.info(f"Base existante chargée : {len(db.get('questions', []))} question(s)")
        return db
    log.info("Nouvelle base créée")
    return {
        "meta": {
            "nom": "Base des questions écrites - Conseil communal de Schaerbeek",
            "version": "1.0", "date_creation": datetime.now().isoformat(),
            "total_questions": 0,
        },
        "questions": [],
    }


def merge_question_into_db(db: dict, question: dict) -> bool:
    """Remplace l'entrée existante (même id = même année+numéro — un
    re-upload est une correction, pas un doublon) ou l'ajoute, puis retrie."""
    if not question.get("id"):
        return False
    idx = next((i for i, q in enumerate(db["questions"]) if q.get("id") == question["id"]), None)
    if idx is not None:
        db["questions"][idx] = question
    else:
        db["questions"].append(question)
    db["questions"].sort(key=lambda q: (q.get("annee") or 0, q.get("numero") or 0), reverse=True)
    return True


def _rotate_backups():
    backup_dir = Path(CONFIG["BACKUP_DIR"])
    if not backup_dir.exists():
        return
    backups = sorted(backup_dir.glob("questions_ecrites_schaerbeek.backup_*.json"))
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
        shutil.copy2(db_path, backup_dir / f"questions_ecrites_schaerbeek.backup_{stamp}.json")
        _rotate_backups()

    db["meta"]["total_questions"] = len(db["questions"])
    db["meta"]["last_updated"] = datetime.now().isoformat()

    tmp = db_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    tmp.replace(db_path)
    log.info(f"Base sauvegardée : {db['meta']['total_questions']} question(s)")

# ══════════════════════════════════════════════════════════════════════════
# PROGRESSION (traitement par lot)
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
# PIPELINE PRINCIPALE (traitement par lot, Colab)
# ══════════════════════════════════════════════════════════════════════════
def run_pipeline(max_files: Optional[int] = None, dry_run: bool = False,
                 force_reprocess: bool = False) -> dict:
    for d in [CONFIG["DRIVE_ROOT"], CONFIG["OUTPUT_DIR"], CONFIG["BACKUP_DIR"]]:
        Path(d).mkdir(parents=True, exist_ok=True)
    _attach_file_logging()

    log = get_logger()
    log.info("\n" + "█"*60)
    log.info("DÉMARRAGE PIPELINE QUESTIONS ÉCRITES v1.0")
    log.info(f"Modèle : {CONFIG['MODEL']}")
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

    stats = {"total": len(pdfs), "done": 0, "skipped": 0, "failed": 0}
    for pdf_path in tqdm(pdfs, desc="PDFs traités", unit="fichier"):
        fname = pdf_path.name
        if fname in done:
            log.info(f"⏭  Déjà traité : {fname}")
            stats["skipped"] += 1
            continue
        if dry_run:
            text = extract_text_from_pdf(pdf_path)
            log.info(f"[DRY RUN] {fname} : {len(text):,} chars")
            stats["done"] += 1
            continue
        question = process_pdf(pdf_path)
        if question is None:
            stats["failed"] += 1
            (Path(CONFIG["OUTPUT_DIR"]) / f"FAILED_{fname}.txt").write_text(
                f"Échec : {fname}\n{datetime.now().isoformat()}")
            continue
        q_out = Path(CONFIG["OUTPUT_DIR"]) / f"{question['id']}.json"
        with open(q_out, "w", encoding="utf-8") as f:
            json.dump(question, f, ensure_ascii=False, indent=2)
        log.info(f"  Question sauvegardée : {q_out.name}")
        merge_question_into_db(db, question)
        save_database(db)
        done.add(fname)
        save_progress(done)
        stats["done"] += 1

    log.info("\n" + "═"*60)
    log.info("RAPPORT FINAL")
    log.info(f"  Total: {stats['total']} | Traités: {stats['done']} | Ignorés: {stats['skipped']} | Échoués: {stats['failed']}")
    log.info(f"  Base finale : {db['meta']['total_questions']} question(s)")
    log.info("═"*60)
    return db


# UTILISATION COLAB :
#   run_pipeline(max_files=2, dry_run=True)    # test sans API
#   db = run_pipeline()                         # production (tous les PDF du dossier input/)
if __name__ == "__main__":
    db = run_pipeline(max_files=5)
