"""
==============================================================================
 PATCH MULTI-COMMUNES v2 (IDEMPOTENT) pour pv_extraction_pipeline.py
 Python 3.10+ - concu pour Google Colab
==============================================================================

OBJECTIF :
    Etendre la pipeline d'extraction (concue pour Schaerbeek) a plusieurs
    communes SANS modifier le fichier original. La commune est deduite du
    sous-dossier : Drive/input/<commune>/fichier.pdf

CE QUE FAIT LE PATCH :
    1. process_pdf   : ajoute seance["commune"] et prefixe l'id
                       (ex: "evere-PV-2024-01-25")
    2. merge_seance_into_db : cle d'unicite (commune, date) au lieu de
                       date seule -> deux communes peuvent sieger le meme jour

IMPORTANT - IDEMPOTENCE :
    Le patch peut etre re-execute autant de fois que necessaire (cellule
    relancee, session Colab resetee...). L'original de chaque fonction est
    capture UNE SEULE FOIS comme attribut du module (garde hasattr).
    Sans cette garde, une double execution provoque une RecursionError
    (le wrapper capture le wrapper) - bug rencontre et corrige.

USAGE COLAB :
    !pip install pdfplumber anthropic tqdm --quiet
    from google.colab import drive; drive.mount('/content/drive')
    # pv_extraction_pipeline.py doit etre dans PV_Schaerbeek/ sur Drive
    # puis executer ce fichier (ou le coller en cellule) :
    exec(open('/content/drive/MyDrive/PV_Schaerbeek/patch_multi_communes.py').read())
    # enfin :
    import os
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."
    db = pipe.run_pipeline()

A TERME :
    Integrer ces changements directement dans pv_extraction_pipeline.py
    (champ commune natif) et supprimer ce patch.
"""

import sys
import re
from pathlib import Path

sys.path.insert(0, "/content/drive/MyDrive/PV_Schaerbeek")

import pv_extraction_pipeline as pipe

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
COMMUNES_CONNUES = ["schaerbeek", "evere", "saint-josse", "bruxelles"]

# Notes de format par commune (utilisables pour enrichir le prompt LLM)
FORMULES_COMMUNE = {
    "schaerbeek":  "Decisions type 'DECIDE', 'PREND ACTE', 'APPROUVE'.",
    "evere":       "PV bilingues FR/NL ; ignorer la partie neerlandaise.",
    "saint-josse": ("Formule frequente : 'Le Conseil approuve le projet "
                    "de deliberation'. PV bilingues FR/NL, ignorer le NL."),
    "bruxelles":   "Gros volume de points ; interpellations frequentes.",
}


def detect_commune(pdf_path):
    """Deduit la commune du chemin : .../input/<commune>/xxx.pdf
    Retourne 'schaerbeek' par defaut (retro-compatibilite : les PV
    Schaerbeek historiques sont a la racine de input/)."""
    for part in Path(pdf_path).parts:
        if part.lower() in COMMUNES_CONNUES:
            return part.lower()
    return "schaerbeek"


# ---------------------------------------------------------------------------
# GARDE ANTI-DOUBLE-PATCH (idempotence)
# ---------------------------------------------------------------------------
# L'original n'est capture QU'UNE FOIS, stocke comme attribut du module.
# Toute re-execution repart du VRAI original -> jamais de RecursionError.
if not hasattr(pipe, "_process_pdf_original"):
    pipe._process_pdf_original = pipe.process_pdf
if not hasattr(pipe, "_merge_original"):
    pipe._merge_original = pipe.merge_seance_into_db


# ---------------------------------------------------------------------------
# PATCH 1 : process_pdf -> injecte la commune
# ---------------------------------------------------------------------------
def _process_pdf_multi(pdf_path):
    seance = pipe._process_pdf_original(pdf_path)     # toujours l'original
    if seance is not None:
        commune = detect_commune(pdf_path)
        seance["seance"]["commune"] = commune
        old_id = seance["seance"]["id"] or "PV"
        if not old_id.startswith(commune):
            seance["seance"]["id"] = commune + "-" + old_id
    return seance


# ---------------------------------------------------------------------------
# PATCH 2 : merge par cle (commune, date)
# ---------------------------------------------------------------------------
def _merge_multi(db, new_seance):
    log = pipe.get_logger()
    new_date = new_seance["seance"]["date"]
    new_com = new_seance["seance"].get("commune", "schaerbeek")
    if not new_date:
        log.warning("  Seance sans date - ignoree")
        return False

    existing = None
    for s in db["seances"]:
        if (s["seance"]["date"] == new_date
                and s["seance"].get("commune", "schaerbeek") == new_com):
            existing = s
            break

    if existing:
        existing_sps = {p.get("sp") for p in existing["points"]}
        added = updated = 0
        for np_ in new_seance["points"]:
            sp = np_.get("sp")
            if sp not in existing_sps:
                existing["points"].append(np_)
                added += 1
            else:
                idx = next(i for i, p in enumerate(existing["points"])
                           if p.get("sp") == sp)
                score_new = sum(1 for v in np_.values() if v)
                score_old = sum(
                    1 for v in existing["points"][idx].values() if v)
                if score_new > score_old:
                    existing["points"][idx] = np_
                    updated += 1
        existing["points"].sort(key=lambda p: p.get("sp", 999))
        for k, v in new_seance["seance"].items():
            if v and not existing["seance"].get(k):
                existing["seance"][k] = v
        log.info("  [" + new_com + "] " + new_date + " maj : +"
                 + str(added) + " ajoutes, " + str(updated) + " enrichis")
    else:
        db["seances"].append(new_seance)
        db["seances"].sort(key=lambda s: (
            s["seance"].get("commune", ""),
            s["seance"].get("date") or ""))
        log.info("  [" + new_com + "] " + new_date + " ajoutee ("
                 + str(len(new_seance["points"])) + " points)")
    return True


# ---------------------------------------------------------------------------
# APPLICATION (remplace toujours par la version propre)
# ---------------------------------------------------------------------------
pipe.process_pdf = _process_pdf_multi
pipe.merge_seance_into_db = _merge_multi

print("Patch multi-communes v2 applique (idempotent).")
_tests = [
    "/x/input/evere/pv-evere-2024-01-25.pdf",
    "/x/input/saint-josse/pv_28.01.26.pdf",
    "/x/input/pv-conseil-2026.04.22.pdf",
]
for _t in _tests:
    print("  " + detect_commune(_t) + "  <-  .../"
          + "/".join(_t.split("/")[-2:]))
