"""Lecture des trois dimensions d'un point, quelle que soit l'ÈRE de la base.

Le procès-verbal distingue ce qu'un point EST (`type`), comment il a été
TRAITÉ (reporté, retiré, traité) et ce qu'il est DEVENU (approuvé, décidé,
pris pour information). La base a longtemps écrit les trois dans un seul champ
`decision` : « REPORTÉ » y côtoyait « APPROUVÉ » alors qu'un point reporté
n'a rien décidé — il a été renvoyé à une séance ultérieure.

pipeline/split_statut_decision.py sépare les trois dimensions dans la base.
Mais le code et les données ne se déploient pas ensemble, et un PV intégré par
le panneau admin peut arriver à l'ancien format : l'API doit donc lire LES DEUX
FORMES. `dimensions()` en est le seul point de passage — elle rend le même
triplet dans les deux cas, si bien qu'une vue écrite au-dessus ne sait pas, et
n'a pas à savoir, si la base a déjà été séparée.

C'est la condition de la migration : la réponse de l'API doit être IDENTIQUE
avant et après le backfill. Ce que les tests figent (test_statut_dimensions).

POURQUOI IMPORTER LA TABLE DEPUIS pipeline/
    `utils_statut` y est la table unique — celle qu'applique le backfill.
    La recopier ici créerait deux tables destinées à diverger : le jour où
    l'une classerait « RETIRÉ » autrement que l'autre, la base et son API ne
    parleraient plus du même point. pipeline/ est un répertoire frère,
    déployé avec le backend (render.yaml, buildFilter) et déjà rendu
    importable par services/pv_integration.py — même mécanique ici, en
    `append` plutôt qu'en `insert` : la table est un module sans dépendance,
    il n'y a aucune raison de laisser pipeline/ passer devant backend/ sur
    sys.path.
"""
import sys
from pathlib import Path

_PIPELINE_DIR = str(Path(__file__).resolve().parent.parent.parent / "pipeline")
if _PIPELINE_DIR not in sys.path:
    sys.path.append(_PIPELINE_DIR)

from utils_statut import (  # noqa: E402
    STATUT_REPORTE, STATUT_RETIRE, STATUT_TRAITE, STATUTS_TRAITEMENT,
    classer_decision, dimensions, mot_issue,
)

# Réexports : les vues n'ont qu'un module à connaître pour lire un point, la
# table restant définie une seule fois (pipeline/utils_statut.py).
__all__ = [
    "STATUT_REPORTE", "STATUT_RETIRE", "STATUT_TRAITE", "STATUTS_TRAITEMENT",
    "classer_decision", "dimensions", "mot_issue",
]
