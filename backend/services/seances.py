"""Agrégation par séance (onglet « Séances ») : complémentaire à la vue par
élu·e (services.elus) — au lieu d'agréger toutes les interventions d'UNE
personne, liste TOUS les points d'UN PV donné (y compris les points
collectifs/administratifs sans auteur·e individuel·le identifiable), avec
demandeur/répondant résolus via le même registre de noms canoniques (voir
services.people.registry) pour un affichage homogène — et pour chaque point,
un résumé lisible de sa décision/vote et de ses thématiques.
"""
import threading

import lexique_store
from services.statistics import load_db
from utils.text import _DECISION_LABELS, _thematique_label
from utils.video import video_session_map

from services.people.attribution import _TYPE_LABEL, _point_author, _respondents
from services.people.mandats import role_at
from services.people.names import (
    _clean, _is_non_person_video_author, _key, _resolve_display_name,
    _split_person_names, _strip_accents, _titlecase,
)
from services.people.registry import _index, _load_video, _nom_by_key, _pairs, _sig
from services.video_merge import _match_pv_point


def _role_for_key(key, date, idx):
    """Rôle d'une personne à la date d'un point donné : mandats déclaratifs
    (voir services.people.mandats, précis à la date) en priorité, repli sur
    le rôle dominant du registre (services.people.registry, indicatif) —
    None si la personne n'a ni l'un ni l'autre (ex. citoyen·ne sans mandat)."""
    if not key:
        return None
    return role_at(key, date) or (idx.get(key) or {}).get("role")


def _combined_role(keys, date, idx):
    """Rôle combiné d'une mention à plusieurs personnes (ex. « répondant(e)s »
    au pluriel) : « college » si l'une d'elles y est, sinon « conseiller » si
    l'une d'elles y est, sinon None — jamais de mélange contradictoire perdu
    silencieusement."""
    roles = [_role_for_key(k, date, idx) for k in keys]
    if "college" in roles:
        return "college"
    if "conseiller" in roles:
        return "conseiller"
    return None


def _people_list(names, pairs, date, idx, resolve, fallback=None):
    """Personnes d'une mention, UNE PAR ENTRÉE : [{"nom", "role"}], dédupliqué
    en conservant l'ordre du PV.

    Le champ source est souvent composé (« Mme Henry et Mme Harzé », « De
    Herde, Smeysters, Bourgmestre ff ») : l'extraction le découpe déjà (voir
    _respondents/_split_person_names), mais seule la forme RECOLLÉE
    (« Audrey Henry et Justine Harzé ») était exposée. Le filtre par
    intervenant·e de l'onglet Séances en faisait alors une « personne »
    unique, introuvable en cherchant l'un des deux noms. Chaque personne porte
    ici SON rôle à la date du point — plus précis que le rôle combiné du
    point (voir _combined_role), qui écrase un·e conseiller·ère répondant aux
    côtés d'un·e échevin·e.

    `fallback` : mention non résolue en personne (ex. « Le Collège ») —
    conservée comme entrée unique pour ne pas disparaître du filtre.
    """
    out, seen = [], set()
    for n in names:
        nom = resolve(n)
        if not nom or nom in seen:
            continue
        seen.add(nom)
        out.append({"nom": nom, "role": _role_for_key(_key(n, pairs), date, idx)})
    if not out and fallback:
        out.append({"nom": fallback, "role": None})
    return out


def _is_reportee(decision) -> bool:
    """Point renvoyé à une séance ultérieure (« REPORTÉ ») : jamais débattu
    ce jour-là, donc jamais de répondant·e ni de débat filmé à en attendre."""
    return _strip_accents(decision or "").strip().lower().startswith("report")


def _is_retire(decision) -> bool:
    """Point RETIRÉ de l'ordre du jour (« RETIRÉ ») : statut DISTINCT du report
    (le point est ôté, pas renvoyé à une séance ultérieure), mais lui aussi
    jamais débattu ce jour-là — donc pas de répondant·e ni de débat filmé."""
    return _strip_accents(decision or "").strip().lower().startswith("retir")


def _decision_label(decision):
    """Statut canonique d'un point, SANS le détail du vote : « Approuvé »,
    « Décidé », « Pris pour information », « Reporté », « Retiré »… None si
    aucune décision (ex. chapitre vidéo sans point de PV). Ce que
    `_decision_summary` complète ensuite du décompte des voix — mais un
    libellé stable est ce qu'il faut pour regrouper/filtrer les points par
    issue (voir les puces de statut de l'onglet Séances), là où « Approuvé à
    l'unanimité » et « Approuvé (33 pour, 9 contre) » sont un même statut."""
    d = (decision or "").strip()
    if not d:
        return None
    norm = _strip_accents(d).lower()
    # Lexique éditable en priorité, puis libellés en dur, puis repli casse
    # (variantes rares/coquilles non répertoriées, ex. « PREND ACTE +
    # DÉROGATION ART.12 »).
    return (lexique_store.decisions().get(norm) or _DECISION_LABELS.get(norm)
            or (d[:1].upper() + d[1:].lower()))


def _decision_summary(decision, vote):
    """Résumé lisible de l'issue d'un point (décision + vote quand il y en a
    un), pour indiquer explicitement, selon chaque cas, pourquoi il n'y a
    par exemple ni répondant·e ni débat filmé à trouver (point voté sans
    discussion, reporté, pris pour information...) plutôt que de laisser
    croire à une recherche infructueuse. None si la décision est vide."""
    label = _decision_label(decision)
    if not label:
        return None
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
    """Liste des séances, la plus récente en premier — pour la navigation
    par année dans l'onglet « Séances ». Inclut aussi les séances filmées
    sans PV encore extrait/apparié (ex. séance très récente) : sinon elles
    seraient absentes de la liste d'une année pourtant filmée."""
    pv = load_db().get("seances", [])
    session_map = video_session_map()
    pv_dates = set()
    out = []
    for s in pv:
        meta = s.get("seance", {}) or {}
        date = meta.get("date")
        if not date:
            continue
        pv_dates.add(date)
        out.append({
            "date": date,
            "n_points": len(s.get("points", [])),
            "url": meta.get("source_url"),
            "video_url": session_map.get(date),
        })
    for vs in _load_video():
        date = vs.get("date")
        if not date or date in pv_dates:
            continue
        out.append({
            "date": date,
            "n_points": len(vs.get("points") or []),
            "url": None,
            "video_url": vs.get("video_url") or session_map.get(date),
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def seance_detail(date: str):
    """Détail d'une séance : tous les points du PV, avec pour chacun le
    demandeur·se (auteur·e, si attribuable), le/la répondant·e, et le lien
    vidéo — précis si un chapitre correspondant existe (même fusion
    point-à-point que services.people.registry._build_all, voir
    _match_pv_point), sinon le lien générique de séance. Gère aussi une
    séance filmée sans PV encore extrait/apparié : dans ce cas, tous ses
    points viennent du chapitrage vidéo (aucun candidat PV à apparier, donc
    chaque chapitre devient un point à part — même logique que pour un
    chapitre orphelin d'une séance normale, voir plus bas). None si la date
    n'est connue ni côté PV ni côté vidéo."""
    pv = load_db().get("seances", [])
    date = (date or "").strip()
    seance = next((s for s in pv if (s.get("seance") or {}).get("date") == date), None)
    video_seance = next((s for s in _load_video() if s.get("date") == date), None)
    if not seance and not video_seance:
        return None
    meta = (seance or {}).get("seance", {}) or {}
    pairs = _pairs()
    nom_by_key = _nom_by_key()
    session_map = video_session_map()
    idx = _index()

    def resolve(name):
        return _resolve_display_name(name, pairs, nom_by_key)

    points = []
    for p in (seance or {}).get("points", []):
        author, author_key = _point_author(p, pairs, date)
        author_names = _split_person_names(author) if author_key else []
        # Point délibératif (approbation, règlement, convention…) : personne
        # ne l'a « demandé », mais le PV liste souvent qui est INTERVENU au
        # débat. Ce champ était ignoré — seules 9 attributions manuelles
        # faisaient surface, laissant 1 633 points débattus sans aucun nom et
        # introuvables par le filtre « intervenant·e ». On l'affiche donc tel
        # quel, sous le libellé « Intervenant·e·s » (voir TYPE_ACTOR_LABEL).
        #
        # AFFICHAGE SEULEMENT : l'attribution par personne (onglet Par élu·e,
        # registry._build_all) reste inchangée — intervenir dans un débat
        # n'est pas déposer un point, et l'y compter gonflerait les
        # « interventions déposées » de tout le monde. C'est aussi pourquoi
        # ce champ reste inutilisable pour désigner un·e AUTEUR·E : il mêle
        # sans distinction qui soulève la discussion et qui y répond (voir
        # attribution._MANUAL_AUTHOR_OVERRIDES) — la mention du/de la
        # répondant·e est retirée plus bas.
        if not author_names and _TYPE_LABEL.get(p.get("type"), "Point") == "Point":
            author_names = [n for mention in (p.get("intervenants") or [])
                            for n in _split_person_names(mention)]
        resp_names = _respondents(p.get("repondant"), meta)
        resp_keys = [_key(n, pairs) for n in resp_names]
        repondants = _people_list(resp_names, pairs, date, idx, resolve,
                                  fallback=_titlecase(_clean(p.get("repondant") or "")) or None)
        repondant = " et ".join(x["nom"] for x in repondants) or None
        demandeurs = _people_list(author_names, pairs, date, idx, resolve)
        # Un point délibératif n'a pas d'auteur·e : ce que l'attribution y
        # inscrit, ce sont les INTERVENANT·E·S du débat (voir attribution.py,
        # _MANUAL_AUTHOR_OVERRIDES), répondant·e comprise — le champ
        # « intervenants » du PV ne distingue pas qui a soulevé la discussion
        # de qui y a répondu. Ce/cette dernier·ère a déjà sa propre ligne : on
        # ne le/la répète pas parmi les intervenant·e·s. Le point reste
        # trouvable par son nom, via `repondants`.
        if _TYPE_LABEL.get(p.get("type"), "Point") == "Point":
            noms_rep = {x["nom"] for x in repondants}
            demandeurs = [x for x in demandeurs if x["nom"] not in noms_rep]
        demandeur = " et ".join(x["nom"] for x in demandeurs) or None
        points.append({
            "sp": p.get("sp") or 0,
            "type": p.get("type"),
            "type_label": _TYPE_LABEL.get(p.get("type"), "Point"),
            "titre": p.get("titre") or "",
            "demandeur": demandeur,
            # Liste INDIVIDUELLE (voir _people_list) : ce que consomme le
            # filtre par intervenant·e de l'onglet Séances, pour qu'un point
            # à plusieurs répondant·e·s se retrouve en cherchant n'importe
            # lequel de leurs noms. L'affichage, lui, reste la forme recollée
            # ci-dessus : un point montre TOUS ses répondant·e·s.
            "demandeurs": demandeurs,
            "repondants": repondants,
            # Rôle de chacun·e À LA DATE DE CETTE SÉANCE (voir _role_for_key/
            # _combined_role ci-dessus) : un·e même élu·e peut être conseiller·ère
            # sur un point ancien et échevin·e sur un point récent — jamais un
            # rôle unique figé pour toute sa carrière (voir seances.js, filtre
            # de rôle à facettes, qui consomme ces deux champs).
            "demandeur_role": _combined_role([_key(x["nom"], pairs) for x in demandeurs], date, idx),
            "repondant": repondant,
            "repondant_role": _combined_role(resp_keys, date, idx),
            "reporte": _is_reportee(p.get("decision")),
            "retire": _is_retire(p.get("decision")),
            "decision": _decision_summary(p.get("decision"), p.get("vote")),
            # Statut canonique, sans le décompte des voix : c'est lui qui
            # regroupe les points par issue dans les puces de l'onglet Séances.
            "statut": _decision_label(p.get("decision")),
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
    # comme un point séparé). Si `points` est vide (pas de PV), aucun candidat
    # ne peut jamais matcher : chaque chapitre devient naturellement un point
    # à part via la branche "non apparié" ci-dessous.
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
                    "demandeur_role": _role_for_key(_key(vauthor, pairs) if vauthor else None, date, idx),
                    "demandeurs": _people_list([vauthor] if vauthor else [], pairs, date, idx, resolve),
                    "repondant": None,
                    "repondants": [],
                    "repondant_role": None,
                    "reporte": False,
                    "retire": False,
                    "decision": None,
                    "statut": None,
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


# ── SYNTHÈSE PAR ANNÉE (onglet Statistiques) ────────────────────────────────
# Une SEULE passe sur toutes les séances, mise en cache : elle alimente le
# graphe « Activité citoyenne » (qui a besoin du décompte des chapitres vidéo
# sans point de PV, absents de la base des PV) et le graphe des issues.
# Passer par `seance_detail` plutôt que de recompter la base brute est
# délibéré : c'est CE que l'application affiche — types, personnes et
# appariements vidéo compris. Recompter autrement rouvrirait l'écart entre les
# deux vues que ces compteurs servent justement à fermer.
_annees_cache = {"sig": None, "rows": None, "par_date": None, "statuts_par_date": None,
                 "sans_decision_par_date": None}
# La passe coûte ~8 s à froid. Les endpoints FastAPI synchrones tournent dans
# un pool de threads : sans verrou, N requêtes arrivant sur un cache froid la
# refaisaient toutes en parallèle — trois appels simultanés mettaient 34 s
# chacun au lieu de 8 s. Le verrou fait attendre les suivantes le temps que la
# première remplisse le cache, qu'elles trouvent alors chaud.
_annees_lock = threading.Lock()

# Types affichés, dans l'ordre des puces de l'onglet Séances. La somme de ces
# cinq compteurs égale le nombre de points de l'année : c'est la partition que
# les puces promettent, et qu'elles ont déjà rompue une fois (659 affichés
# pour 676 points en 2025). Vérifiée par test.
ANNEE_TYPE_ORDER = ["Point", "Motion", "Question orale", "Demande", "Débat filmé"]


def annees_stats() -> list:
    """Une ligne par année : nombre de séances et de points, répartition par
    type, et rapprochement des personnes.

    Aucune vue ne l'affiche plus (les deux tableaux de contrôle par année ont
    été retirés de l'onglet Statistiques) : ces lignes sont désormais lues par
    les tests, qui y vérifient la partition des types et les deux identités du
    rapprochement ci-dessous. Elles sont construites de toute façon par la
    passe que partagent hors_pv_par_date() et statuts_par_date(), donc les
    garder ne coûte rien de plus qu'un compteur par point.

    Le rapprochement répond à une question simple — « puis-je retrouver le
    total en agrégeant par intervenant·e ? » — dont la réponse est non, pour
    deux raisons de sens contraire, que ces colonnes rendent explicites :

        points          = points_avec_personne + points_sans_personne
        somme_par_personne = points_avec_personne + surplus

    En moins, les points qui ne nomment personne (points administratifs ou
    collectifs, ~70 % du corpus) ; en plus, le surplus des points à plusieurs
    personnes, comptés une fois par chacune. Seule une UNION dédoublonnée des
    points couverts retombe sur le total."""
    _ensure_annees()
    return _annees_cache["rows"]


def hors_pv_par_date() -> dict:
    """Nombre de chapitres vidéo sans point de PV, PAR DATE de séance — pour
    le niveau MOIS du graphe d'activité, qui agrège par date côté client.
    Certaines de ces séances n'ont aucun PV extrait : elles n'existent que
    dans le chapitrage, d'où une map par date plutôt qu'un champ ajouté au
    résumé des séances du PV."""
    _ensure_annees()
    return _annees_cache["par_date"]


def statuts_par_date() -> dict:
    """{date de séance: {statut: nb de points}} — l'issue des points, par
    séance. Par DATE et non par année : le graphe des statuts descend
    année → mois → séance, et agrège donc à trois niveaux depuis la même
    source. Les points sans décision (chapitres vidéo, débats non tranchés)
    n'y figurent pas."""
    _ensure_annees()
    return _annees_cache["statuts_par_date"]


def sans_decision_par_date() -> dict:
    """Points de PV dont AUCUNE décision n'a été relevée, PAR DATE de séance.

    Le compte exclut les chapitres vidéo sans point de PV (type « Débat filmé »),
    qui n'ont pas de décision par construction et ne figurent pas non plus dans
    le décompte de points du graphe d'activité. Ce qui reste — 67 points sur
    10 062, concentrés sur quelques séances — est l'écart exact entre les deux
    graphes, que le graphe des issues affiche plutôt que de le laisser
    inexpliqué :

        points de l'année = Σ des statuts + sans décision

    (invariant vérifié par test, sur les 17 années)."""
    _ensure_annees()
    return _annees_cache["sans_decision_par_date"]


def _ensure_annees():
    sig = _sig()
    if _annees_cache["sig"] == sig:
        return
    with _annees_lock:
        # Re-test sous verrou : la requête qui attendait vient peut-être de se
        # faire remplir le cache par celle qui la précédait.
        if _annees_cache["sig"] != sig:
            _build_annees(sig)


def _build_annees(sig):
    """Passe unique sur toutes les séances. Toujours appelée sous _annees_lock."""
    # Toutes les dates connues : celles des PV, plus les séances filmées dont
    # le PV n'est pas (encore) extrait — elles figurent dans la liste des
    # séances, leurs chapitres sont donc des points de l'année.
    dates = {(s.get("seance") or {}).get("date") for s in load_db().get("seances", [])}
    dates |= {s.get("date") for s in _load_video()}
    dates = sorted(d for d in dates if d)

    par_annee = {}
    par_date = {}
    statuts = {}
    sans_decision = {}
    for date in dates:
        detail = seance_detail(date)
        if not detail:
            continue
        row = par_annee.setdefault(date[:4], {
            "annee": date[:4], "seances": 0, "points": 0,
            "types": {t: 0 for t in ANNEE_TYPE_ORDER},
            "points_avec_personne": 0, "somme_par_personne": 0, "_noms": set(),
        })
        row["seances"] += 1
        for p in detail["points"]:
            row["points"] += 1
            if p["type_label"] in row["types"]:
                row["types"][p["type_label"]] += 1
            if p["type_label"] == "Débat filmé":
                par_date[date] = par_date.get(date, 0) + 1
            if p.get("statut"):
                st = statuts.setdefault(date, {})
                st[p["statut"]] = st.get(p["statut"], 0) + 1
            elif p["type_label"] != "Débat filmé":
                # Point de PV sans décision relevée — voir sans_decision_par_date.
                sans_decision[date] = sans_decision.get(date, 0) + 1
            # Dédoublonné PAR POINT : quelqu'un présent des deux côtés d'un
            # même point ne le compte qu'une fois.
            noms = {x["nom"] for x in (p.get("demandeurs") or []) + (p.get("repondants") or [])}
            if noms:
                row["points_avec_personne"] += 1
                row["somme_par_personne"] += len(noms)
                row["_noms"] |= noms

    rows = []
    for annee in sorted(par_annee):
        row = par_annee[annee]
        row["intervenants"] = len(row.pop("_noms"))
        row["points_sans_personne"] = row["points"] - row["points_avec_personne"]
        row["surplus"] = row["somme_par_personne"] - row["points_avec_personne"]
        rows.append(row)

    _annees_cache["sig"] = sig
    _annees_cache["rows"] = rows
    _annees_cache["par_date"] = par_date
    _annees_cache["statuts_par_date"] = statuts
    _annees_cache["sans_decision_par_date"] = sans_decision
