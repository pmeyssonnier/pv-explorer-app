"""Chargement + cache (mtime) de la base des questions écrites — même
mécanique que services.statistics.load_db pour pv_conseil_schaerbeek.json.
Fichier optionnel : absent tant qu'aucune question écrite n'a été publiée,
auquel cas on retourne une base vide plutôt que d'échouer (comportement
attendu au tout premier déploiement de la fonctionnalité)."""
import json
import os

QE_JSON_PATH = os.environ.get("QE_JSON_PATH", "questions_ecrites_schaerbeek.json")

_EMPTY_DB = {
    "meta": {
        "nom": "Base des questions écrites - Conseil communal de Schaerbeek",
        "version": "1.0", "total_questions": 0,
    },
    "questions": [],
}

_cache = {"mtime": None, "db": None}


def load_qe_db() -> dict:
    if not os.path.exists(QE_JSON_PATH):
        return _EMPTY_DB
    mtime = os.path.getmtime(QE_JSON_PATH)
    if _cache["mtime"] != mtime:
        with open(QE_JSON_PATH, encoding="utf-8") as f:
            _cache["db"] = json.load(f)
        _cache["mtime"] = mtime
    return _cache["db"]
