"""
╔══════════════════════════════════════════════════════════════════════════╗
║  SCRAPER PV CONSEIL COMMUNAL SCHAERBEEK (1030.be) → GOOGLE DRIVE        ║
║  Google Colab - Python 3.10+                          VERSION 2.3        ║
║  Scrape https://www.1030.be/fr/proces-verbaux                            ║
║  et copie tous les PDF dans Google Drive                                 ║
╚══════════════════════════════════════════════════════════════════════════╝

CHANGELOG v2.3 :
  - Nomenclature courte des archives 2010 confirmée : `pv-YYYY-MM-DD-sp.pdf`
    (SANS "conseil", contrairement à toutes les nomenclatures plus récentes) —
    ex : pv-2010-12-22-sp.pdf, vu sur https://www.1030.be/fr/proces-verbaux
    (pagination). is_pv_pdf() et URL_TEMPLATES la reconnaissent désormais.

CHANGELOG v2.2 :
  - Patterns d'URL confirmés : 2 emplacements (/import/ et /document/) et
    3 nomenclatures (tirets `pv-conseil-`, underscores `pv_conseil_`, espaces
    `PV Conseil` URL-encodées). is_pv_pdf() reconnaît les trois.

CHANGELOG v2.1 (corrections code review Opus 4.8) :
  - FIX #1 : suppression du paramètre `log=None` inutile (check/download_missing)
  - FIX #2 : probe_url vérifie content-type ET taille (rejette les 404 "doux")
  - FIX #3 : candidats triés et dédupliqués avant sondage (compte tqdm correct)
  - DESIGN : dates de séances GÉNÉRÉES automatiquement (dernier mercredi/mois)
             au lieu de la liste statique — CONFIRMED_DATES pour les exceptions
  - DESIGN : remplacement du walrus operator par une helper lisible

INSTRUCTIONS COLAB :
  Cellule 1 : !pip install requests beautifulsoup4 selenium tqdm --quiet
              !apt-get install -y chromium-chromedriver --quiet
  Cellule 2 : from google.colab import drive; drive.mount('/content/drive')
  Cellule 3 : Colle ce script et exécute run_scraper()

STRATÉGIE :
  La page 1030.be charge les liens PDF via JavaScript ET bloque les IP cloud.
  Depuis Colab (IP Google) ça fonctionne. Deux méthodes combinées :
    A) Selenium (charge le JS) → récupère les liens <a href="*.pdf">
    C) Reconstruction d'URL par pattern (sondage proactif)
       Patterns confirmés (2 emplacements) :
         /data/media/import/pv_conseil_YYYY.MM.DD_sp.pdf   (underscores)
         /data/media/import/PV%20Conseil%20YYYY.MM.DD.pdf  (espaces, PV anciens)
         /data/media/document/pv-conseil-YYYY.MM.DD.pdf    (tirets, avec/sans -sp)
"""

# ══════════════════════════════════════════════════════════════════════════
# SECTION 1 — INSTALLATION (à coller dans une cellule Colab séparée)
# ══════════════════════════════════════════════════════════════════════════
INSTALL_COMMANDS = """
# ── Cellule d'installation (exécuter une seule fois par session) ──────────
!pip install requests beautifulsoup4 selenium tqdm --quiet
!apt-get install -y chromium-chromedriver --quiet

# Vérification
import requests, bs4, selenium, tqdm
print("✅ Tous les modules installés")
"""

# ══════════════════════════════════════════════════════════════════════════
# SECTION 2 — IMPORTS
# ══════════════════════════════════════════════════════════════════════════
import re
import time
import json
import logging
import hashlib
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional
from tqdm import tqdm

# ══════════════════════════════════════════════════════════════════════════
# SECTION 3 — CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════
CONFIG = {
    # ── URL cible ─────────────────────────────────────────────────────────
    "BASE_URL":       "https://www.1030.be",
    "PV_PAGE_FR":     "https://www.1030.be/fr/proces-verbaux",
    "PV_PAGE_NL":     "https://www.1030.be/nl/notulen",
    # Pattern racine des PDF
    "PDF_BASE_PATH":  "/data/media/document/",

    # ── Google Drive ──────────────────────────────────────────────────────
    "DRIVE_ROOT":     "/content/drive/MyDrive/PV_Schaerbeek",
    "PDF_SUBDIR":     "input",        # sous-dossier pour les PDF bruts
    "MANIFEST_FILE":  "download_manifest.json",  # log de tous les téléchargements

    # ── Comportement ──────────────────────────────────────────────────────
    # Ne pas re-télécharger les fichiers déjà présents
    "SKIP_EXISTING":  True,
    # Délai entre téléchargements (secondes) — courtois envers le serveur
    "DELAY_SEC":      1.0,
    # Timeout pour chaque requête HTTP
    "TIMEOUT_SEC":    30,
    # Nombre max de tentatives par fichier
    "MAX_RETRIES":    3,
    # Années à couvrir (None = toutes)
    "YEARS_FILTER":   None,   # ex: [2023, 2024, 2025, 2026]

    # ── Selenium ──────────────────────────────────────────────────────────
    "SELENIUM_TIMEOUT": 15,    # secondes d'attente chargement page
}

# ══════════════════════════════════════════════════════════════════════════
# SECTION 4 — LOGGER
# ══════════════════════════════════════════════════════════════════════════
def setup_logger(log_path: Optional[str] = None):
    handlers = [logging.StreamHandler()]
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers
    )
    return logging.getLogger("pv_scraper")

# ══════════════════════════════════════════════════════════════════════════
# SECTION 5 — MÉTHODE A : SELENIUM (charge le JavaScript)
# ══════════════════════════════════════════════════════════════════════════
def scrape_with_selenium(url: str, log) -> list[str]:
    """
    Ouvre la page avec Selenium, attend le chargement JS,
    extrait tous les liens .pdf.
    Retourne une liste d'URLs absolues.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except ImportError:
        log.error("Selenium non installé. Exécuter : !pip install selenium")
        return []

    log.info(f"  Selenium → ouverture de {url}")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    driver = None
    pdf_urls = []
    try:
        driver = webdriver.Chrome(options=options)
        driver.get(url)

        # Attendre que des liens apparaissent sur la page
        try:
            WebDriverWait(driver, CONFIG["SELENIUM_TIMEOUT"]).until(
                EC.presence_of_element_located((By.TAG_NAME, "a"))
            )
            # Attendre un peu plus pour le JS asynchrone
            time.sleep(3)
        except Exception:
            log.warning("  Timeout Selenium — la page a peut-être quand même chargé")

        # Chercher tous les liens PDF
        links = driver.find_elements(By.TAG_NAME, "a")
        for link in links:
            href = link.get_attribute("href") or ""
            if ".pdf" in href.lower():
                abs_url = make_absolute(href)
                if abs_url and is_pv_pdf(abs_url):
                    pdf_urls.append(abs_url)

        log.info(f"  Selenium → {len(pdf_urls)} liens PDF trouvés")

        # Chercher aussi dans le code source rendu
        page_source = driver.page_source
        extra = extract_pdf_links_from_html(page_source)
        for u in extra:
            if u not in pdf_urls:
                pdf_urls.append(u)
        log.info(f"  Selenium (source) → total {len(pdf_urls)} liens PDF")

    except Exception as e:
        log.error(f"  Erreur Selenium : {e}")
    finally:
        if driver:
            driver.quit()

    return list(set(pdf_urls))


# ══════════════════════════════════════════════════════════════════════════
# SECTION 6 — MÉTHODE B : REQUESTS + BEAUTIFULSOUP (fallback)
# ══════════════════════════════════════════════════════════════════════════
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-BE,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def scrape_with_requests(url: str, log) -> list[str]:
    """
    Méthode légère sans JS : récupère les liens PDF présents dans le HTML statique.
    Moins efficace si les liens sont chargés par JS, mais utile en complément.
    """
    from bs4 import BeautifulSoup
    log.info(f"  Requests → GET {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=CONFIG["TIMEOUT_SEC"])
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        pdf_urls = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            abs_url = make_absolute(href)
            if abs_url and is_pv_pdf(abs_url):
                pdf_urls.append(abs_url)
        log.info(f"  Requests → {len(pdf_urls)} liens PDF trouvés")
        return list(set(pdf_urls))
    except Exception as e:
        log.error(f"  Erreur requests : {e}")
        return []


def extract_pdf_links_from_html(html: str) -> list[str]:
    """Extrait tous les liens PDF d'une chaîne HTML."""
    # Regex pour trouver les URLs PDF dans le HTML
    patterns = [
        r'href=["\']([^"\']*\.pdf)["\']',
        r'src=["\']([^"\']*\.pdf)["\']',
        r'"url"\s*:\s*"([^"]*\.pdf)"',
        r"'url'\s*:\s*'([^']*\.pdf)'",
        # Liens en data-* attributes
        r'data-[a-z-]+=["\']([^"\']*\.pdf)["\']',
    ]
    found = []
    for pat in patterns:
        for match in re.finditer(pat, html, re.IGNORECASE):
            href = match.group(1)
            abs_url = make_absolute(href)
            if abs_url and is_pv_pdf(abs_url):
                found.append(abs_url)
    return list(set(found))


# ══════════════════════════════════════════════════════════════════════════
# SECTION 7 — MÉTHODE C : RECONSTRUCTION D'URL PAR PATTERN (proactive)
# ══════════════════════════════════════════════════════════════════════════
# Patterns confirmés : /import/pv_conseil_YYYY.MM.DD_sp.pdf,
#   /import/PV%20Conseil%20YYYY.MM.DD.pdf, /document/pv-conseil-YYYY.MM.DD[-sp[_N]].pdf
#
# DESIGN : au lieu d'une liste statique à maintenir manuellement, on GÉNÈRE
# les dates probables (le Conseil communal de Schaerbeek siège en général le
# dernier mercredi de chaque mois, hors juillet/août). On peut aussi ajouter
# des dates confirmées connues dans CONFIRMED_DATES (elles priment).

import calendar as _calendar


def last_wednesday(year: int, month: int) -> str:
    """Retourne le dernier mercredi du mois au format YYYY.MM.DD."""
    # weekday() : lundi=0 ... mercredi=2 ... dimanche=6
    last_day = _calendar.monthrange(year, month)[1]
    d = last_day
    while datetime(year, month, d).weekday() != 2:  # 2 = mercredi
        d -= 1
    return f"{year:04d}.{month:02d}.{d:02d}"


def generate_seance_dates(start_year: int = 2020,
                          end_year: Optional[int] = None) -> list[str]:
    """
    Génère les dates probables de séances : dernier mercredi de chaque mois,
    en excluant juillet et août (pas de conseil l'été à Schaerbeek).
    """
    if end_year is None:
        end_year = datetime.now().year
    dates = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            if month in (7, 8):        # relâche estivale
                continue
            # ne pas générer de dates dans le futur
            candidate = last_wednesday(year, month)
            cand_dt = datetime.strptime(candidate, "%Y.%m.%d")
            if cand_dt <= datetime.now():
                dates.append(candidate)
    return dates


# Dates confirmées manuellement (priment sur les dates générées) —
# utile quand une séance n'est pas le dernier mercredi (séance extraordinaire, etc.)
CONFIRMED_DATES = [
    "2025.05.28", "2025.12.17", "2026.03.25", "2026.04.22",
]


def get_all_seance_dates() -> list[str]:
    """Fusionne dates générées automatiquement + dates confirmées, triées, dédupliquées."""
    generated = generate_seance_dates()
    return sorted(set(generated) | set(CONFIRMED_DATES))


# Alias rétrocompatible : certaines fonctions référencent encore KNOWN_SEANCE_DATES
KNOWN_SEANCE_DATES = get_all_seance_dates()

# {date} = date à points (YYYY.MM.DD) — c'est la forme utilisée dans les noms
# de fichiers 1030.be. Deux EMPLACEMENTS confirmés coexistent : /import/ (souvent
# les PV plus anciens) et /document/ (PV plus récents).
URL_TEMPLATES = [
    # ── /import/ — underscores, la date conserve ses points ──────────────────
    #   ex : /import/pv_conseil_2024.01.24_sp.pdf
    "https://www.1030.be/data/media/import/pv_conseil_{date}_sp.pdf",
    "https://www.1030.be/data/media/import/pv_conseil_{date}.pdf",
    # ── /import/ ancienne nomenclature avec espaces (URL-encodés %20) ─────────
    #   ex : /import/PV%20Conseil%202017.02.22.pdf
    "https://www.1030.be/data/media/import/PV%20Conseil%20{date}.pdf",
    # ── /document/ — tirets, avec ou sans suffixe -sp / -sp_N ─────────────────
    #   ex : /document/pv-conseil-2025.05.21.pdf  et  ...-2024.02.21-sp.pdf
    "https://www.1030.be/data/media/document/pv-conseil-{date}.pdf",
    "https://www.1030.be/data/media/document/pv-conseil-{date}-sp.pdf",
    "https://www.1030.be/data/media/document/pv-conseil-{date}-sp_{n}.pdf",
    "https://www.1030.be/data/media/document/pv-conseil-{date}-sp_{n}_0.pdf",
    # ── /document/ — nomenclature courte des archives 2010, SANS "conseil" ────
    #   Date à TIRETS ici (pas à points) : ex. /document/pv-2010-12-22-sp.pdf
    "https://www.1030.be/data/media/document/pv-{date_dash}-sp.pdf",
    "https://www.1030.be/data/media/document/pv-{date_dash}.pdf",
    "https://www.1030.be/data/media/document/pv-{date_dash}-sp_{n}.pdf",
    # ── Variantes historiques (underscores dans la date), par sécurité ───────
    "https://www.1030.be/data/media/document/pv-conseil-{date_under}-sp_{n}.pdf",
    "https://www.1030.be/data/media/document/pv-conseil-{date_under}-sp.pdf",
    "https://www.1030.be/data/media/document/pv_conseil_{date_under}_sp_{n}.pdf",
    # ── Variantes NL ─────────────────────────────────────────────────────────
    "https://www.1030.be/data/media/document/notulen-raad-{date}-{n}.pdf",
    "https://www.1030.be/data/media/document/notulen-{date}.pdf",
]


def generate_candidate_urls(dates: list[str]) -> list[str]:
    """
    Génère toutes les URLs candidates pour une liste de dates.
    Inclut les suffixes 0, 1, 2 pour les séances avec plusieurs documents.
    """
    candidates = []
    for date_dot in dates:
        date_under = date_dot.replace(".", "_")
        date_dash = date_dot.replace(".", "-")

        # Suffixes numériques : 0 = principal, 1/2 = documents supplémentaires
        for n in range(4):
            for tmpl in URL_TEMPLATES:
                url = tmpl.format(
                    date=date_dot,
                    date_under=date_under,
                    date_dash=date_dash,
                    n=n
                )
                candidates.append(url)

        # Variantes avec date_dash dans le path
        for n in range(4):
            candidates.extend([
                f"https://www.1030.be/data/media/document/pv-conseil-{date_dash}-sp_{n}.pdf",
                f"https://www.1030.be/data/media/document/pv-conseil-{date_dash}-sp.pdf",
            ])

    return list(set(candidates))


def probe_url(url: str, session: requests.Session) -> bool:
    """
    Vérifie si une URL PDF existe réellement.
    FIX #2 : rejette les "404 douces" (HTTP 200 renvoyant du HTML ou un contenu trop petit).
    On vérifie : statut 200 + content-type PDF + taille crédible (> 1 Ko).
    """
    try:
        resp = session.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return False
        ctype = resp.headers.get("content-type", "").lower()
        clen = int(resp.headers.get("content-length", "0") or 0)
        # Un vrai PV fait plusieurs centaines de Ko ; un faux 200 renvoie ~21 octets ("Host...")
        is_pdf = "pdf" in ctype or "octet-stream" in ctype
        big_enough = clen == 0 or clen > 1000   # clen=0 = serveur ne donne pas la taille → on tolère
        return is_pdf and big_enough
    except Exception:
        return False


def discover_urls_by_probe(dates: list[str], log, sample_mode: bool = False) -> list[str]:
    """
    Teste toutes les URLs candidates et garde celles qui existent.
    sample_mode=True : teste seulement les 10 premières dates (test rapide).
    """
    if sample_mode:
        dates = dates[-5:]  # dernières 5 dates (les plus récentes)
        log.info(f"  Mode test : sonde seulement les {len(dates)} dernières dates")

    candidates = sorted(generate_candidate_urls(dates))  # déjà dédupliqué + ordre stable
    log.info(f"  {len(candidates)} URLs candidates uniques — sondage en cours...")

    session = requests.Session()
    found = []

    for url in tqdm(candidates, desc="Sondage URLs", unit="url"):
        if probe_url(url, session):
            found.append(url)
            log.info(f"  ✓ Trouvé : {url.split('/')[-1]}")
        time.sleep(0.1)  # très léger délai

    log.info(f"  Sondage terminé : {len(found)} PDF confirmés")
    return found


# ══════════════════════════════════════════════════════════════════════════
# SECTION 8 — HELPERS URL
# ══════════════════════════════════════════════════════════════════════════
def make_absolute(href: str) -> Optional[str]:
    """Transforme un href relatif en URL absolue."""
    if not href:
        return None
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return CONFIG["BASE_URL"] + href
    return None


# Nomenclature courte confirmée sur les archives 2010 : pv-YYYY-MM-DD[-sp].pdf,
# SANS "conseil" — ex: /data/media/document/pv-2010-12-22-sp.pdf. Un simple
# mot-clé serait trop permissif (n'importe quel "pv-*.pdf" matcherait) ; on
# exige la forme datée pour rester spécifique aux PV.
_SHORT_DATE_PV_RE = re.compile(r"/pv-20\d{2}-\d{2}-\d{2}")


def is_pv_pdf(url: str) -> bool:
    """Vérifie si l'URL correspond à un PV du conseil communal.
    Couvre les 2 emplacements (/import/, /document/), les 3 nomenclatures
    récentes (tirets `pv-conseil-`, underscores `pv_conseil_`, espaces
    `PV Conseil`) et la nomenclature courte des archives 2010 (sans "conseil",
    voir _SHORT_DATE_PV_RE)."""
    u = url.lower()
    if not (u.endswith(".pdf") and "1030.be" in u):
        return False
    if any(k in u for k in [
        "pv-conseil", "pv_conseil", "pv%20conseil", "pv conseil",
        "notulen", "proces-verbal",
    ]):
        return True
    return bool(_SHORT_DATE_PV_RE.search(u))


def url_to_filename(url: str) -> str:
    """
    Dérive un nom de fichier propre depuis l'URL.
    Ex: .../pv-conseil-2026.04.22-sp_0.pdf → pv-conseil-2026_04_22-sp_0.pdf
    """
    filename = url.split("/")[-1]
    # Normalise les points de date en underscores sauf l'extension
    parts = filename.rsplit(".", 1)
    if len(parts) == 2:
        name = parts[0].replace(".", "_")
        # Mais si le nom contient des patterns de date YYYY_MM_DD, garde-les
        filename = f"{name}.{parts[1]}"
    return filename


def extract_date_from_url(url: str) -> Optional[str]:
    """Extrait la date YYYY-MM-DD depuis une URL de PV."""
    m = re.search(r"(\d{4})[._\-](\d{2})[._\-](\d{2})", url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


# ══════════════════════════════════════════════════════════════════════════
# SECTION 9 — TÉLÉCHARGEMENT
# ══════════════════════════════════════════════════════════════════════════
def download_pdf(
    url: str,
    dest_path: Path,
    session: requests.Session,
    log
) -> dict:
    """
    Télécharge un PDF vers dest_path.
    Retourne un dict avec le résultat : {url, filename, size, status, date}.
    """
    filename = dest_path.name
    result = {
        "url": url,
        "filename": filename,
        "date": extract_date_from_url(url),
        "size_bytes": 0,
        "status": "error",
        "downloaded_at": None,
        "sha256": None,
    }

    if dest_path.exists() and CONFIG["SKIP_EXISTING"]:
        result["status"] = "skipped"
        result["size_bytes"] = dest_path.stat().st_size
        return result

    for attempt in range(CONFIG["MAX_RETRIES"]):
        try:
            resp = session.get(
                url,
                headers=HEADERS,
                timeout=CONFIG["TIMEOUT_SEC"],
                stream=True
            )
            resp.raise_for_status()

            # Vérification content-type
            ct = resp.headers.get("content-type", "")
            if "pdf" not in ct.lower() and "octet-stream" not in ct.lower():
                log.warning(f"  Content-type inattendu pour {filename}: {ct}")

            # Écriture
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            sha256 = hashlib.sha256()
            total_bytes = 0
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        sha256.update(chunk)
                        total_bytes += len(chunk)

            result["size_bytes"] = total_bytes
            result["status"] = "downloaded"
            result["downloaded_at"] = datetime.now().isoformat()
            result["sha256"] = sha256.hexdigest()
            return result

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                result["status"] = "not_found"
                return result  # Inutile de réessayer un 404
            log.warning(f"  Erreur HTTP {e.response.status_code} pour {filename} (tentative {attempt+1})")
            time.sleep(2 ** attempt)
        except requests.exceptions.Timeout:
            log.warning(f"  Timeout pour {filename} (tentative {attempt+1})")
            time.sleep(5)
        except Exception as e:
            log.warning(f"  Erreur {type(e).__name__} pour {filename} (tentative {attempt+1}): {e}")
            time.sleep(3)

    return result


# ══════════════════════════════════════════════════════════════════════════
# SECTION 10 — MANIFEST
# ══════════════════════════════════════════════════════════════════════════
def load_manifest(manifest_path: Path) -> dict:
    """Charge le fichier de suivi des téléchargements."""
    if manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            return json.load(f)
    return {"downloads": [], "last_updated": None, "total_files": 0, "total_bytes": 0}


def save_manifest(manifest: dict, manifest_path: Path):
    """Sauvegarde le fichier de suivi."""
    manifest["last_updated"] = datetime.now().isoformat()
    manifest["total_files"] = len([d for d in manifest["downloads"] if d["status"] == "downloaded"])
    manifest["total_bytes"] = sum(d.get("size_bytes", 0) for d in manifest["downloads"] if d["status"] == "downloaded")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════
# SECTION 11 — PIPELINE PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════
def run_scraper(
    use_selenium: bool = True,
    use_probe: bool = True,
    probe_sample_only: bool = False,
    years_filter: Optional[list[int]] = None,
    dry_run: bool = False,
    max_downloads: Optional[int] = None
):
    """
    Pipeline complète de scraping.

    Args:
        use_selenium      : Utiliser Selenium pour parser la page (recommandé)
        use_probe         : Sonder les URLs candidates par pattern
        probe_sample_only : Sonder seulement les 5 dernières dates (test)
        years_filter      : Limiter à certaines années (ex: [2025, 2026])
        dry_run           : Lister les PDF sans les télécharger
        max_downloads     : Limiter le nombre de téléchargements (test)
    """
    # Setup
    drive_root = Path(CONFIG["DRIVE_ROOT"])
    pdf_dir = drive_root / CONFIG["PDF_SUBDIR"]
    manifest_path = drive_root / CONFIG["MANIFEST_FILE"]
    log_path = str(drive_root / "scraper.log")

    pdf_dir.mkdir(parents=True, exist_ok=True)
    log = setup_logger(log_path)

    log.info("\n" + "█"*60)
    log.info("DÉMARRAGE SCRAPER PV 1030.be → Google Drive")
    log.info(f"Destination : {pdf_dir}")
    log.info("█"*60)

    manifest = load_manifest(manifest_path)
    already_downloaded = {d["url"] for d in manifest["downloads"] if d["status"] == "downloaded"}
    log.info(f"Déjà téléchargés (manifest) : {len(already_downloaded)} fichiers")

    # ── COLLECTE DES URLS ─────────────────────────────────────────────────
    all_pdf_urls = set()

    # Méthode A : Selenium
    if use_selenium:
        log.info("\n── Méthode A : Selenium ───────────────────────────────────")
        try:
            for page_url in [CONFIG["PV_PAGE_FR"], CONFIG["PV_PAGE_NL"]]:
                urls = scrape_with_selenium(page_url, log)
                all_pdf_urls.update(urls)
        except Exception as e:
            log.error(f"  Selenium indisponible ({e}) — passage au fallback")
            # Fallback Requests
            for page_url in [CONFIG["PV_PAGE_FR"], CONFIG["PV_PAGE_NL"]]:
                urls = scrape_with_requests(page_url, log)
                all_pdf_urls.update(urls)
    else:
        log.info("\n── Méthode A : Requests (sans JS) ─────────────────────────")
        for page_url in [CONFIG["PV_PAGE_FR"], CONFIG["PV_PAGE_NL"]]:
            urls = scrape_with_requests(page_url, log)
            all_pdf_urls.update(urls)

    # Méthode C : Sondage par pattern
    if use_probe:
        log.info("\n── Méthode C : Sondage par pattern d'URL ──────────────────")
        # Filtrer les dates selon years_filter
        dates_to_probe = KNOWN_SEANCE_DATES
        if years_filter:
            dates_to_probe = [d for d in KNOWN_SEANCE_DATES if int(d[:4]) in years_filter]
        log.info(f"  Dates à sonder : {len(dates_to_probe)}")
        probed = discover_urls_by_probe(dates_to_probe, log, sample_mode=probe_sample_only)
        all_pdf_urls.update(probed)

    # Filtre years
    if years_filter:
        before = len(all_pdf_urls)

        def _in_years(u: str) -> bool:
            d = extract_date_from_url(u)
            return bool(d) and int(d[:4]) in years_filter

        all_pdf_urls = {u for u in all_pdf_urls if _in_years(u)}
        log.info(f"  Filtre années {years_filter} : {before} → {len(all_pdf_urls)} URLs")

    log.info(f"\nTotal URLs collectées : {len(all_pdf_urls)}")

    if not all_pdf_urls:
        log.warning("Aucune URL trouvée ! Vérifier la connectivité ou les patterns.")
        return

    # Trier par date (plus récentes en premier)
    sorted_urls = sorted(
        all_pdf_urls,
        key=lambda u: extract_date_from_url(u) or "0000-00-00",
        reverse=True
    )

    # ── DRY RUN ───────────────────────────────────────────────────────────
    if dry_run:
        log.info("\n[DRY RUN] Liste des PDF qui seraient téléchargés :")
        for i, url in enumerate(sorted_urls):
            fname = url_to_filename(url)
            date = extract_date_from_url(url) or "date inconnue"
            status = "⏭ déjà présent" if url in already_downloaded else "⬇ à télécharger"
            log.info(f"  {i+1:3d}. [{date}] {fname} — {status}")
        return

    # ── TÉLÉCHARGEMENT ────────────────────────────────────────────────────
    if max_downloads:
        sorted_urls = sorted_urls[:max_downloads]

    log.info(f"\n── Téléchargement de {len(sorted_urls)} fichiers ───────────────")
    session = requests.Session()
    stats = {"downloaded": 0, "skipped": 0, "not_found": 0, "error": 0, "bytes": 0}

    for url in tqdm(sorted_urls, desc="Téléchargement PDF", unit="fichier"):
        filename = url_to_filename(url)
        dest_path = pdf_dir / filename
        date = extract_date_from_url(url) or "?"

        if url in already_downloaded and CONFIG["SKIP_EXISTING"] and dest_path.exists():
            log.debug(f"  ⏭ {filename}")
            stats["skipped"] += 1
            continue

        result = download_pdf(url, dest_path, session, log)

        if result["status"] == "downloaded":
            size_kb = result["size_bytes"] // 1024
            log.info(f"  ✅ [{date}] {filename} ({size_kb} Ko)")
            stats["downloaded"] += 1
            stats["bytes"] += result["size_bytes"]
        elif result["status"] == "skipped":
            stats["skipped"] += 1
        elif result["status"] == "not_found":
            log.debug(f"  ✗ 404 {filename}")
            stats["not_found"] += 1
        else:
            log.error(f"  ❌ Échec {filename}")
            stats["error"] += 1

        # Ajouter au manifest
        manifest["downloads"].append(result)
        save_manifest(manifest, manifest_path)

        time.sleep(CONFIG["DELAY_SEC"])

    # ── RAPPORT ───────────────────────────────────────────────────────────
    total_mb = stats["bytes"] / (1024 * 1024)
    log.info("\n" + "═"*60)
    log.info("RAPPORT FINAL")
    log.info(f"  Téléchargés    : {stats['downloaded']} fichiers ({total_mb:.1f} Mo)")
    log.info(f"  Déjà présents  : {stats['skipped']}")
    log.info(f"  Non trouvés    : {stats['not_found']}")
    log.info(f"  Erreurs        : {stats['error']}")
    log.info(f"  Destination    : {pdf_dir}")
    log.info(f"  Manifest       : {manifest_path}")
    log.info("═"*60)

    # Afficher résumé par année
    print("\n📁 FICHIERS PAR ANNÉE dans Drive :")
    by_year = {}
    for f in sorted(pdf_dir.glob("*.pdf")):
        m = re.search(r"(\d{4})", f.name)
        if m:
            y = m.group(1)
            by_year[y] = by_year.get(y, 0) + 1
    for year in sorted(by_year.keys(), reverse=True):
        print(f"  {year} : {by_year[year]} fichiers")

    return manifest


# ══════════════════════════════════════════════════════════════════════════
# SECTION 12 — UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════
def list_drive_pdfs(year: Optional[int] = None) -> list[Path]:
    """Liste les PDF déjà dans Google Drive."""
    pdf_dir = Path(CONFIG["DRIVE_ROOT"]) / CONFIG["PDF_SUBDIR"]
    all_pdfs = sorted(pdf_dir.glob("*.pdf"))
    if year:
        all_pdfs = [p for p in all_pdfs if str(year) in p.name]
    print(f"Fichiers PDF dans Drive ({len(all_pdfs)}) :")
    for p in all_pdfs:
        size_kb = p.stat().st_size // 1024
        date = extract_date_from_url(p.name) or "?"
        print(f"  [{date}] {p.name} ({size_kb} Ko)")
    return all_pdfs


def check_missing_dates() -> list[str]:
    """Compare les dates connues avec les fichiers présents et signale les manquants."""
    pdf_dir = Path(CONFIG["DRIVE_ROOT"]) / CONFIG["PDF_SUBDIR"]
    present_dates = set()
    for f in pdf_dir.glob("*.pdf"):
        d = extract_date_from_url(f.name)
        if d:
            present_dates.add(d)

    missing = []
    for date_dot in KNOWN_SEANCE_DATES:
        date_iso = date_dot[:4] + "-" + date_dot[5:7] + "-" + date_dot[8:]
        if date_iso not in present_dates:
            missing.append(date_iso)

    print(f"\n📋 Séances manquantes ({len(missing)}) :")
    for d in missing:
        print(f"  ⚠ {d}")
    return missing


def download_missing():
    """Télécharge uniquement les séances manquantes."""
    missing = check_missing_dates()
    if not missing:
        print("✅ Toutes les séances connues sont présentes !")
        return

    years_needed = sorted(set(int(d[:4]) for d in missing))
    print(f"\n🔄 Tentative de récupération des {len(missing)} séances manquantes...")
    run_scraper(
        use_selenium=False,  # Sondage suffisant pour les manquants
        use_probe=True,
        probe_sample_only=False,
        years_filter=years_needed,
    )


# ══════════════════════════════════════════════════════════════════════════
# SECTION 13 — POINT D'ENTRÉE COLAB
# ══════════════════════════════════════════════════════════════════════════
"""
═══════════════════════════════════════════════════════════════
UTILISATION DANS GOOGLE COLAB
═══════════════════════════════════════════════════════════════

# ── CELLULE 1 — Installation ──────────────────────────────────────────────
!pip install requests beautifulsoup4 selenium tqdm --quiet
!apt-get install -y chromium-chromedriver --quiet
print("✅ Installation OK")

# ── CELLULE 2 — Google Drive ──────────────────────────────────────────────
from google.colab import drive
drive.mount('/content/drive')

# ── CELLULE 3 — Charger le script ─────────────────────────────────────────
# Option A : depuis Drive (si tu y as copié le fichier)
import sys
sys.path.insert(0, '/content/drive/MyDrive/PV_Schaerbeek')
from pv_scraper_1030 import *

# Option B : coller directement le code dans la cellule

# ── CELLULE 4 — TEST (dry run, sans télécharger) ──────────────────────────
run_scraper(
    use_selenium=False,   # True si Selenium installé
    use_probe=True,
    probe_sample_only=True,  # Sonde seulement les 5 dernières dates
    dry_run=True             # Montre ce qui serait téléchargé
)

# ── CELLULE 5 — PRODUCTION : télécharger toutes les séances ──────────────
manifest = run_scraper(
    use_selenium=True,   # Récupère les liens JS de la page officielle
    use_probe=True,      # Complète avec sondage par pattern
    probe_sample_only=False,
    dry_run=False
)

# ── CELLULE 6 — Télécharger seulement 2025-2026 ──────────────────────────
run_scraper(years_filter=[2025, 2026], use_probe=True)

# ── CELLULE 7 — Vérifier les manquants ───────────────────────────────────
check_missing_dates()

# ── CELLULE 8 — Télécharger les manquants ────────────────────────────────
download_missing()

# ── CELLULE 9 — Lister les PDF dans Drive ────────────────────────────────
list_drive_pdfs()
list_drive_pdfs(year=2026)

# ── APRÈS LE SCRAPING : lancer le pipeline d'extraction ──────────────────
# Les PDF sont maintenant dans /content/drive/MyDrive/PV_Schaerbeek/input/
# Lance la pipeline d'extraction :
from pv_extraction_pipeline import *
db = run_pipeline()
═══════════════════════════════════════════════════════════════
"""

if __name__ == "__main__":
    # Test local (hors Colab)
    run_scraper(
        use_selenium=False,
        use_probe=True,
        probe_sample_only=True,
        dry_run=True
    )
