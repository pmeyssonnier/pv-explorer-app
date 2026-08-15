"""
==============================================================================
 SCRAPER EVERE - publi.irisnet.be (plateforme regionale Editoria) -> Drive
 Version 3 (finale) - Python 3.10+ - concu pour Google Colab
==============================================================================

USAGE COLAB :
    !pip install requests beautifulsoup4 --quiet
    from google.colab import drive; drive.mount('/content/drive')
    # coller/importer ce fichier puis :
    run_scraper_evere(years=[2024], dry_run=True)    # verification
    run_scraper_evere(dry_run=False)                 # telechargement complet

STRUCTURE EDITORIA (reverse-engineering, voir scraping/README.md) :
    Organisation (vipKey prefixe O)
      -> Categories annee (prefixe C)         : "2015"..."2026"
        -> Categories seance (prefixe C)      : "Conseil communal du ..."
          -> Fichiers : /web/download?pubKey=P...  (prefixe P)
    Les liens seance : <a class="click-show-content" data-bk="C...">
    FR et NL partagent le meme pubKey -> dedup par pubKey.

PIEGES CONNUS :
    - Annees 2015-2019 : vides sur la plateforme (archives non publiees).
    - Formats de dates INCOHERENTS par annee :
        2020 : "2020.11.26 - 26 novembre"
        2021 : "Conseil communal du 28-01-2021"   (DD-MM-YYYY)
        2022 : "Conseil communal du 01-27-2022"   (MM-DD-YYYY, format US !)
        2023 : "Conseil communal du 2023-01-26"   (YYYY-MM-DD)
        2024+: "Conseil communal du 2024.01.25"   (YYYY.MM.DD)
      -> extract_date_any() desambiguise (nombre >12 = jour).
    - Le site communal evere.brussels bloque les IP cloud (403) ;
      publi.irisnet.be repond aussi depuis Colab (IP Google).
    - Nommage anti-collision : si aucune date extraite, le nom du fichier
      inclut un slug du titre + pubKey court (jamais d'ecrasement).
"""

import re
import time
import requests
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
BASE = "https://publi.irisnet.be"
PAGE_WEB = BASE + "/web/"
DRIVE_DIR = Path("/content/drive/MyDrive/PV_Schaerbeek/input/evere")

# Categorie parente "Conseil communal" d'Evere : permet de decouvrir
# automatiquement les categories annee (2026 et suivantes incluses).
KEY_CONSEIL_COMMUNAL = "C02f81f14-c560-48f0-93c5-b41fd3e4d591"

session = requests.Session()
session.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def get_soup(vipkey):
    url = BASE + "/web/category?vipKey=" + vipkey + "&locale=fr"
    r = session.get(url, timeout=30)
    return BeautifulSoup(r.text, "html.parser")


def discover_year_keys():
    """Decouvre les categories annee (auto-maintenance : futures annees OK)."""
    soup = get_soup(KEY_CONSEIL_COMMUNAL)
    year_keys = {}
    for a in soup.find_all("a", class_="click-show-content"):
        titre = a.get_text(strip=True)
        bk = a.get("data-bk", "")
        if re.fullmatch(r"20\d{2}", titre) and bk.startswith("C"):
            year_keys[int(titre)] = bk
    return year_keys


def extract_date_any(titre):
    """
    Date ISO (YYYY-MM-DD) depuis un titre de seance Evere.
    Gere les 4 formats observes + desambiguisation DD-MM vs MM-DD.
    """
    m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", titre)
    if m:
        y, a, b = m.group(1), int(m.group(2)), int(m.group(3))
        if a > 12:                      # YYYY-DD-MM improbable mais gere
            a, b = b, a
        return y + "-" + str(a).zfill(2) + "-" + str(b).zfill(2)
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](20\d{2})", titre)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12:                      # 28-01 -> jour-mois (2021)
            jour, mois = a, b
        elif b > 12:                    # 01-27 -> mois-jour US (2022)
            jour, mois = b, a
        else:                           # ambigu -> europeen DD-MM
            jour, mois = a, b
        return y + "-" + str(mois).zfill(2) + "-" + str(jour).zfill(2)
    return None


def list_seances(vipkey_annee):
    """Sous-categories 'seance' d'une annee (exclut la navigation)."""
    soup = get_soup(vipkey_annee)
    seances = []
    for a in soup.find_all("a", class_="click-show-content"):
        titre = a.get_text(strip=True)
        bk = a.get("data-bk", "")
        if not bk.startswith("C"):
            continue
        if re.fullmatch(r"20\d{2}", titre) or titre in (
                "Transparence", "Conseil communal", "A.C. Evere",
                "Retour à la liste", "Annexes"):
            continue
        seances.append({"bk": bk, "titre": titre,
                        "date": extract_date_any(titre)})
    return seances


def list_fichiers(vipkey_seance):
    """Fichiers telechargeables d'une seance (dedup FR/NL par pubKey)."""
    soup = get_soup(vipkey_seance)
    fichiers = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "download" in href.lower() and "pubKey=" in href:
            fichiers.append({"url": urljoin(PAGE_WEB, href),
                             "label": a.get_text(strip=True)})
    par_cle = {}
    for f in fichiers:
        m = re.search(r"pubKey=(P[0-9a-f-]+)", f["url"])
        if not m:
            continue
        cle = m.group(1)
        est_fr = "fran" in f["label"].lower()
        if cle not in par_cle or est_fr:
            par_cle[cle] = f
    return list(par_cle.values())


def download_pdf(url, dest_path):
    """Telecharge et verifie le magic %PDF. Retourne (ok, taille|erreur)."""
    try:
        r = session.get(url, timeout=60)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            dest_path.write_bytes(r.content)
            return True, len(r.content)
        return False, r.status_code
    except Exception as e:
        return False, str(e)[:60]


# ---------------------------------------------------------------------------
# SCRAPER PRINCIPAL
# ---------------------------------------------------------------------------
def run_scraper_evere(years=None, dry_run=True):
    """
    Scrape les PV d'Evere depuis publi.irisnet.be vers DRIVE_DIR.
      years   : liste d'annees (None = toutes celles decouvertes)
      dry_run : True = lister sans telecharger
    Reprise sure : les fichiers deja presents sont sautes.
    """
    DRIVE_DIR.mkdir(parents=True, exist_ok=True)
    vipkeys = discover_year_keys()
    print("Annees disponibles sur Editoria : " + str(sorted(vipkeys)))
    cibles = years or sorted(vipkeys)
    total_dl, total_skip, total_err = 0, 0, 0

    for year in cibles:
        key = vipkeys.get(year)
        if not key:
            print("\nANNEE " + str(year) + " : pas de categorie (ignoree)")
            continue
        print()
        print("=" * 55)
        print("ANNEE " + str(year))
        seances = list_seances(key)
        print("  " + str(len(seances)) + " seances")

        for s in seances:
            fichiers = list_fichiers(s["bk"])
            time.sleep(0.6)                     # courtoisie serveur
            for i, f in enumerate(fichiers):
                m = re.search(r"pubKey=(P[0-9a-f]{8})", f["url"])
                pk = m.group(1) if m else "Pxxxx"
                if s["date"]:
                    suffix = "" if i == 0 else "_" + str(i)
                    fname = "pv-evere-" + s["date"] + suffix + ".pdf"
                else:
                    slug = re.sub(r"[^a-z0-9]+", "-",
                                  s["titre"].lower()).strip("-")[:40]
                    fname = "pv-evere-" + slug + "-" + pk + ".pdf"

                dest = DRIVE_DIR / fname
                if dry_run:
                    print("    [DRY] " + fname)
                    continue
                if dest.exists():
                    total_skip += 1
                    continue
                ok, info = download_pdf(f["url"], dest)
                if ok:
                    print("    OK  " + fname + " ("
                          + format(info // 1024, ",") + " Ko)")
                    total_dl += 1
                else:
                    print("    ERR " + fname + " : " + str(info))
                    total_err += 1
                time.sleep(1.0)

    print()
    print("=" * 55)
    print("TERMINE : " + str(total_dl) + " telecharges, "
          + str(total_skip) + " deja presents, "
          + str(total_err) + " erreurs")
    print("Dossier : " + str(DRIVE_DIR))


if __name__ == "__main__":
    # Hors Colab : adapter DRIVE_DIR puis lancer un dry run
    run_scraper_evere(years=[2024], dry_run=True)
