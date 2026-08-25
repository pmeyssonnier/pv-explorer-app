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
import re
import sys
from pathlib import Path

_PIPELINE_DIR = str(Path(__file__).resolve().parent.parent.parent / "pipeline")
if _PIPELINE_DIR not in sys.path:
    sys.path.append(_PIPELINE_DIR)

from utils.text import _strip_accents  # noqa: E402
from utils_statut import (  # noqa: E402
    STATUT_REPORTE, STATUT_RETIRE, STATUT_TRAITE, STATUTS_TRAITEMENT,
    classer_decision, dimensions, mot_issue,
)

# Réexports : les vues n'ont qu'un module à connaître pour lire un point, la
# table restant définie une seule fois (pipeline/utils_statut.py).
__all__ = [
    "STATUT_REPORTE", "STATUT_RETIRE", "STATUT_TRAITE", "STATUTS_TRAITEMENT",
    "classer_decision", "decision_recherchee", "dimensions", "mot_issue",
]


# ── Ce que la recherche vectorielle ne sait pas trouver ────────────────────
# Trois issues sont RARES dans le corpus — 10 rejets, 6 retraits, 645 reports
# sur 10 062 points — et ne tiennent qu'à UN MOT dans un texte vectorisé
# dominé par le titre et le résumé. Résultat : à la question « le conseil
# a-t-il déjà rejeté des motions ? », les 30 passages les plus proches sont
# des motions quelconques, et l'app répondait « je n'en trouve aucune » alors
# que les dix étaient indexées. Le mot ne pèse pas assez pour se distinguer.
#
# Le champ `decision` est une métadonnée Pinecone : quand la question porte
# sur l'une de ces issues, une recherche FILTRÉE dessus va les chercher là où
# la ressemblance générale ne les fait pas remonter (voir services.rag).
#
# Volontairement limité à ces trois-là : « APPROUVÉ » ou « DÉCIDE » comptent
# des milliers de points, le top 30 en contient déjà, un filtre n'y ajouterait
# rien. Ce sont les issues rares qui ont besoin d'être repêchées.
_ISSUES_RARES = (
    (re.compile(r"\brejet(?:e|ee|es|ees)\b"), "REJETÉ"),
    (re.compile(r"\bretir(?:e|ee|es|ees)\b|\bretrait\b"), "RETIRÉ"),
    (re.compile(r"\breport(?:e|ee|es|ees)\b|\bajourn"), "REPORTÉ"),
)


def decision_recherchee(question: str):
    """L'issue rare sur laquelle porte la question, à filtrer côté index.

    Le PARTICIPE est requis (« rejetée », « reportés »), pas le substantif :
    « le rejet des eaux usées » est un sujet de séance, pas une question sur
    le sort d'un point. « retrait » et « ajournement » font exception — ils
    n'ont pas d'autre sens ici.

    Rend au plus une issue, la plus rare d'abord : une question qui parle de
    rejet ET de report cherche d'abord ce qui est presque introuvable.
    None si la question ne porte sur aucune — le cas courant.
    """
    q = _strip_accents(question or "").lower()
    for motif, decision in _ISSUES_RARES:
        if motif.search(q):
            return decision
    return None

