"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LES TROIS DIMENSIONS D'UN POINT — TYPE / TRAITEMENT / DÉCISION           ║
╚══════════════════════════════════════════════════════════════════════════╝

Le procès-verbal distingue trois choses que le champ `decision` mélange :

    ce qu'EST le point        →  `type`                (déjà séparé)
    comment il a été TRAITÉ   →  `statut_traitement`   traité / reporté / retiré
    ce qu'il est DEVENU       →  `decision`            approuvé, décidé, pris acte…

Un point REPORTÉ est renvoyé à une séance ultérieure, un point RETIRÉ est ôté
de l'ordre du jour : ni l'un ni l'autre n'a rien décidé — le conseil ne s'est
pas prononcé sur le fond. Un point « DÉBAT » n'a rien décidé non plus : il a
été discuté, et la discussion est un DÉROULEMENT, pas une issue. Ranger ces
trois mots parmi les décisions revient à répondre « la réunion a été reportée »
à la question « qu'a décidé le conseil ? ».

Ce module est le SEUL endroit qui dit dans quelle case va un mot écrit dans
`decision`. Les scripts qui séparent la base (split_statut_decision) et les
vues qui la lisent partagent cette table, pour qu'un « REPORTÉ » signifie la
même chose partout.

CE QUE `debat` NE DIT PAS
    `debat = True` signifie que le PV a inscrit « DÉBAT » comme issue du point.
    `False` ne dit PAS qu'il n'y a pas eu de discussion : un point approuvé
    après une heure d'échanges porte « APPROUVÉ », et ce champ-là n'en garde
    aucune trace. On ne l'infère jamais depuis `intervenants` — ce serait
    déduire un champ d'un autre, exactement ce que la séparation évite.

DÉTERMINISTE SUR 100 % DU CORPUS : les 22 graphies présentes dans la base se
rangent sans ambiguïté (report → reporté, retir → retiré, débat → débat, tout
le reste → une vraie décision). Aucun PDF relu, aucun appel à Claude.
"""
import re
import unicodedata

# Le point a-t-il été traité en séance, ou soustrait à la délibération ?
#   traité   le conseil s'est prononcé (ou a débattu, ou n'avait rien à décider)
#   reporté  renvoyé à une séance ultérieure — reviendra
#   retiré   ôté de l'ordre du jour — ne reviendra pas sous ce numéro
# Deux statuts DISTINCTS : le pipeline les sépare déjà à l'extraction
# (RE_REPORTE / RE_RETIRE), ne les confondre nulle part en aval.
STATUT_TRAITE = "traité"
STATUT_REPORTE = "reporté"
STATUT_RETIRE = "retiré"
STATUTS_TRAITEMENT = frozenset({STATUT_TRAITE, STATUT_REPORTE, STATUT_RETIRE})

# Mention du vote parfois recollée à la décision par le PV (« Pris acte à
# l'unanimité », « Reporté (33 pour, 0 contre) »). Le classement porte sur le
# libellé nu : sans ce retrait, « REPORTÉ à l'unanimité » resterait rangé parmi
# les décisions. Même intention que services.seances._MENTION_VOTE, ici sur la
# base brute plutôt que sur l'affichage.
_MENTION_VOTE = re.compile(
    r"\s*(?:[-–—,(]\s*)?(?:à\s+l'unanimit[ée]|\d+\s*pour\b.*|par\s+\d+\s*voix\b.*)\)?\s*$",
    re.IGNORECASE,
)


def _norm(v) -> str:
    """Texte comparable : sans accents, minuscule, sans mention de vote."""
    s = v if isinstance(v, str) else ("" if v is None else str(v))
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return _MENTION_VOTE.sub("", s).strip().lower()


def classer_decision(decision):
    """Range une `decision` brute du PV dans les trois dimensions.

    Retourne `(statut_traitement, decision, debat)` :

        « REPORTÉ »   → ("reporté", None,       False)   le fond n'a pas été jugé
        « RETIRÉ »    → ("retiré",  None,       False)   idem
        « DÉBAT »     → ("traité",  None,       True)    discuté, rien décidé
        « APPROUVÉ »  → ("traité",  "APPROUVÉ", False)   décision réelle, INTACTE
        ""            → ("traité",  "",         False)   régime normal d'une
                                                         question orale, d'une
                                                         demande d'habitant·e…

    La décision n'est JAMAIS réécrite ni normalisée : soit elle est effacée
    parce que ce n'en était pas une, soit elle est rendue telle quelle. La
    canonisation des libellés (« PRENDS POUR INFORMATION » → « Pris pour
    information ») reste l'affaire de l'affichage — utils.text._DECISION_LABELS
    et le lexique éditable —, pas du stockage.
    """
    n = _norm(decision)
    if n.startswith("report"):
        return STATUT_REPORTE, None, False
    if n.startswith("retir"):
        return STATUT_RETIRE, None, False
    if n == "debat":
        return STATUT_TRAITE, None, True
    return STATUT_TRAITE, decision, False
