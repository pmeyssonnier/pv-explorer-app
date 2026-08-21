"""Agrégation par séance (onglet « Séances ») : complémentaire à la vue par
élu·e (services.elus) — au lieu d'agréger toutes les interventions d'UNE
personne, liste TOUS les points d'UN PV donné (y compris les points
collectifs/administratifs sans auteur·e individuel·le identifiable), avec
demandeur/répondant résolus via le même registre de noms canoniques (voir
services.people.registry) pour un affichage homogène — et pour chaque point,
un résumé lisible de sa décision/vote et de ses thématiques.
"""
from services.statistics import load_db
from utils.video import video_session_map

from services.people.attribution import _TYPE_LABEL, _point_author, _respondents
from services.people.names import (
    _clean, _is_non_person_video_author, _key, _resolve_display_name,
    _split_person_names, _strip_accents, _titlecase,
)
from services.people.registry import _load_video, _nom_by_key, _pairs
from services.video_merge import _match_pv_point


def _is_reportee(decision) -> bool:
    """Point renvoyé à une séance ultérieure (« REPORTÉ ») : jamais débattu
    ce jour-là, donc jamais de répondant·e ni de débat filmé à en attendre."""
    return _strip_accents(decision or "").strip().lower().startswith("report")


def _thematique_label(t: str) -> str:
    """Étiquette d'affichage d'une thématique (slug interne, ex.
    « transports_publics ») : espaces au lieu des soulignés, casse
    homogène. Plus de 4500 valeurs distinctes dans le corpus (souvent
    rares/spécifiques) : pas de dictionnaire de correction des accents
    (perdus dans les slugs), simple normalisation."""
    s = (t or "").replace("_", " ").replace("-", " ").strip()
    return s[:1].upper() + s[1:] if s else s


# Libellés d'affichage homogènes pour le champ « decision » du PV, dont la
# graphie brute varie (casse, accents, coquilles ponctuelles — ex. « PRENDS
# POUR INFORMATION », « PRENDRE ACTE »). Clé = texte sans accents/minuscule.
_DECISION_LABELS = {
    "approuve": "Approuvé",
    "decide": "Décidé",
    "decidé": "Décidé",
    "debat": "Débat",
    "reporte": "Reporté",
    "prend acte": "Pris acte",
    "prendre acte": "Pris acte",
    "prend pour information": "Pris pour information",
    "prendre pour information": "Pris pour information",
    "prends pour information": "Pris pour information",
    "prises pour information": "Pris pour information",
    "arrete": "Arrêté",
    "nomme": "Nommé",
    "admet": "Admis",
    "beslist": "Décidé",
    "minute de silence": "Minute de silence",
}


def _decision_summary(decision, vote):
    """Résumé lisible de l'issue d'un point (décision + vote quand il y en a
    un), pour indiquer explicitement, selon chaque cas, pourquoi il n'y a
    par exemple ni répondant·e ni débat filmé à trouver (point voté sans
    discussion, reporté, pris pour information...) plutôt que de laisser
    croire à une recherche infructueuse. None si la décision est vide."""
    d = (decision or "").strip()
    if not d:
        return None
    norm = _strip_accents(d).lower()
    label = _DECISION_LABELS.get(norm)
    if not label:
        # Repli pour les variantes rares/coquilles non répertoriées
        # (ex. « PREND ACTE + DÉROGATION ART.12 ») : casse homogène.
        label = d[:1].upper() + d[1:].lower()
    vote = vote if isinstance(vote, dict) else {}
    vtype = vote.get("type")
    if vtype == "unanimite":
        return f"{label} à l'unanimité"
    if vtype == "vote_nominal":
        pour = vote.get("pour")
        contre = vote.get("contre") or 0
        abst = vote.get("abstentions") or 0
        bits = []
        if pour is not None:
            bits.append(f"{pour} pour")
        bits.append(f"{contre} contre")
        bits.append(f"{abst} abstention{'s' if abst != 1 else ''}")
        return f"{label} ({', '.join(bits)})"
    return label


def seances_list() -> list:
    """Liste des séances (PV), la plus récente en premier — pour la
    navigation par année dans l'onglet « Séances »."""
    pv = load_db().get("seances", [])
    session_map = video_session_map()
    out = []
    for s in pv:
        meta = s.get("seance", {}) or {}
        date = meta.get("date")
        if not date:
            continue
        out.append({
            "date": date,
            "n_points": len(s.get("points", [])),
            "url": meta.get("source_url"),
            "video_url": session_map.get(date),
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def seance_detail(date: str):
    """Détail d'une séance : tous les points du PV, avec pour chacun le
    demandeur·se (auteur·e, si attribuable), le/la répondant·e, et le lien
    vidéo — précis si un chapitre correspondant existe (même fusion
    point-à-point que services.people.registry._build_all, voir
    _match_pv_point), sinon le lien générique de séance. None si la date
    est inconnue."""
    pv = load_db().get("seances", [])
    date = (date or "").strip()
    seance = next((s for s in pv if (s.get("seance") or {}).get("date") == date), None)
    if not seance:
        return None
    meta = seance.get("seance", {}) or {}
    pairs = _pairs()
    nom_by_key = _nom_by_key()
    session_map = video_session_map()

    def resolve(name):
        return _resolve_display_name(name, pairs, nom_by_key)

    points = []
    for p in seance.get("points", []):
        author, author_key = _point_author(p, pairs, date)
        author_names = _split_person_names(author) if author_key else []
        author_resolved = list(dict.fromkeys(filter(None, (resolve(n) for n in author_names))))
        demandeur = " et ".join(author_resolved) if author_resolved else None
        resp_names = _respondents(p.get("repondant"), meta)
        resp_resolved = list(dict.fromkeys(filter(None, (resolve(n) for n in resp_names))))
        repondant = " et ".join(resp_resolved) if resp_resolved else (
            _titlecase(_clean(p.get("repondant") or "")) or None
        )
        points.append({
            "sp": p.get("sp") or 0,
            "type": p.get("type"),
            "type_label": _TYPE_LABEL.get(p.get("type"), "Point"),
            "titre": p.get("titre") or "",
            "demandeur": demandeur,
            "repondant": repondant,
            "reporte": _is_reportee(p.get("decision")),
            "decision": _decision_summary(p.get("decision"), p.get("vote")),
            "thematiques": [_thematique_label(t) for t in (p.get("thematiques") or [])],
            "montant_eur": p.get("montant_eur"),
            "url": meta.get("source_url"),
            "video_url": session_map.get(date),
            "video_precise": False,
            "_author_key": author_key,
        })

    # Fusion avec le chapitrage vidéo de cette séance, point par point (même
    # logique que services.people.registry._build_all : un chapitre vidéo
    # dont l'auteur·e et le sujet correspondent à un point déjà listé
    # remplace son lien générique par le lien précis, plutôt que d'apparaître
    # comme un point séparé).
    video_seance = next((s for s in _load_video() if s.get("date") == date), None)
    if video_seance:
        for vp in video_seance.get("points", []):
            vauthor = vp.get("auteur")
            titre = vp.get("titre_fr") or vp.get("titre") or ""
            deeplink = vp.get("deeplink")
            if vauthor and not _is_non_person_video_author(vauthor):
                vk = _key(vauthor, pairs)
                candidates = [pt for pt in points if pt["_author_key"] == vk] if vk else []
                match = _match_pv_point(titre, candidates) if candidates else None
            else:
                # Chapitre collectif (motion/point du collège sans auteur·e
                # individuel·le nommé·e, ex. « point_urgent ») : impossible
                # de restreindre par personne, donc comparaison au titre de
                # TOUS les points de la séance (hors chapitres déjà ajoutés
                # ci-dessous, jamais entre eux) — seuil bien plus élevé
                # (0.6 au lieu de 0.35) car le bassin de candidats est
                # nettement plus large, validé empiriquement sur le corpus
                # réel (au-dessus, correspondances toujours correctes ;
                # en-dessous, non fiable). Sans cette branche, ~59% des
                # chapitres vidéo (ceux sans auteur·e, ex. motions
                # collectives du Collège) étaient silencieusement omis de
                # cette vue plutôt que fusionnés ou même affichés à part.
                pv_points = [pt for pt in points if pt["type"] != "video"]
                match = _match_pv_point(titre, pv_points, threshold=0.6) if pv_points else None
            if match is not None:
                if deeplink:
                    match["video_url"] = deeplink
                    match["video_precise"] = True
            else:
                # Chapitre vidéo sans point PV correspondant identifié avec
                # confiance : affiché à part plutôt que silencieusement omis
                # (voir _match_pv_point — jamais de fusion à l'aveugle).
                points.append({
                    "sp": 0,
                    "type": "video",
                    "type_label": _TYPE_LABEL["video"],
                    "titre": titre,
                    "demandeur": resolve(vauthor),
                    "repondant": None,
                    "reporte": False,
                    "decision": None,
                    "thematiques": [],
                    "montant_eur": None,
                    "url": deeplink,
                    "video_url": vp.get("video_url") or video_seance.get("video_url"),
                    "video_precise": False,
                    "_author_key": None,
                })

    for pt in points:
        pt.pop("_author_key", None)
    points.sort(key=lambda x: x["sp"])

    video_url = (video_seance.get("video_url") if video_seance else None) or session_map.get(date)

    return {
        "date": date,
        "url": meta.get("source_url"),
        "video_url": video_url,
        "n_points": len(points),
        "points": points,
    }
