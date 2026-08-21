"""Fusion PV ↔ chapitrage vidéo : retrouve, pour un chapitre vidéo donné, le
point PV correspondant (même sujet), pour n'afficher qu'UNE intervention avec
le lien vidéo précis plutôt que deux entrées séparées pour le même point.
"""
import difflib
import re

from services.people.names import _strip_accents


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", _strip_accents(t or "").lower())


def _match_pv_point(video_titre: str, candidates: list, threshold: float = 0.35) -> dict | None:
    """Retrouve, parmi des points PV candidats, celui qui correspond au point
    vidéo (même sujet) — pour fusionner en UNE intervention plutôt que d'en
    afficher deux (« Demande »/« Motion » + « Débat filmé ») pour le même
    point, quand la séance a été filmée ET chapitrée. Les titres PV sont
    souvent plus courts que les titres de chapitrage vidéo (qui ajoutent
    « Demande de M./Mme X » + la traduction NL) : une simple inclusion de
    chaîne suffit la plupart du temps ; en cas d'ambiguïté, on utilise la
    similarité textuelle avec un seuil prudent — jamais de fusion à
    l'aveugle (mieux vaut deux entrées séparées qu'une fusion fausse).

    `threshold` doit être plus élevé quand `candidates` n'est pas déjà
    restreint à la même personne (chapitres collectifs sans auteur·e
    individuel·le, comparés à TOUS les points de la séance — bassin de
    candidats bien plus large, donc plus de risque de score élevé fortuit) :
    voir seances.seance_detail, validé empiriquement à 0.6 sur le corpus réel."""
    if len(candidates) == 1:
        return candidates[0]
    vn = _norm_title(video_titre)
    contains = []
    for c in candidates:
        cn = _norm_title(c["titre"])
        if cn and (cn in vn or vn in cn):
            contains.append(c)
    if len(contains) == 1:
        return contains[0]
    pool = contains if contains else candidates
    best = max(pool, key=lambda c: difflib.SequenceMatcher(None, vn, _norm_title(c["titre"])).ratio())
    score = difflib.SequenceMatcher(None, vn, _norm_title(best["titre"])).ratio()
    return best if score >= threshold else None
