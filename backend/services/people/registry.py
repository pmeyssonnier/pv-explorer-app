"""Registre par personne : construit, à partir des PV et du chapitrage vidéo,
l'index de toutes les interventions déposées/réponses par personne, le
registre de résolution de noms (paires prénom/nom) et la map clé→nom
canonique — le tout en cache mémoire (invalidé par mtime des fichiers
sources, qui ne changent qu'au redéploiement).

Classification conseiller·ère / Collège (rôle dominant) incluse : dérivée de
l'activité observée, pas d'une source déclarative.
"""
import json
import os
from collections import defaultdict

import lexique_store
from services.statistics import load_db
from services.questions_ecrites import QE_JSON_PATH, load_qe_db
from utils.text import _thematique_label
from utils.video import video_session_map

from services.people.attribution import _author_of, _point_author, _respondents
from services.people.mandats import current_role
from services.people.names import (
    _DISPLAY_NAME_OVERRIDES, _best_display_variant, _clean, _is_non_person_video_author,
    _is_role_token, _key, _norm_tok, _split_person_names, _titlecase,
)
from services.video_merge import _match_pv_point

_VIDEO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "video_conseil_schaerbeek.json"
)


def _load_video():
    try:
        with open(_VIDEO_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    return data.get("seances", data) if isinstance(data, dict) else data


def _sig():
    """Signature (mtime des fichiers sources) pour invalider le cache si l'un
    d'eux est remplacé sur place (dev / redéploiement)."""
    parts = []
    try:
        from config import PV_JSON_PATH
        parts.append(os.path.getmtime(PV_JSON_PATH) if os.path.exists(PV_JSON_PATH) else 0)
    except Exception:
        parts.append(0)
    parts.append(os.path.getmtime(_VIDEO_PATH) if os.path.exists(_VIDEO_PATH) else 0)
    parts.append(os.path.getmtime(QE_JSON_PATH) if os.path.exists(QE_JSON_PATH) else 0)
    return tuple(parts)


def _role_of(n_depose: int, n_repond: int) -> str:
    """Étiquette de rôle dominante, dérivée de l'activité (indicatif) — repli
    quand les mandats déclaratifs (services.people.mandats) ne couvrent pas
    la personne, voir `_current_role_of` ci-dessous.

    Le champ "répondant" d'un point n'est renseigné que par le membre du
    Collège qui a répondu ; une personne qui répond au moins une fois et
    ne dépose jamais de point est donc, structurellement, un·e échevin·e
    ou le bourgmestre, même avec peu de séances (ex. mandat écourté).
    """
    if n_repond > 0 and n_depose == 0:
        return "college"
    if n_repond >= 8 and n_repond > n_depose:
        return "college"      # échevin·e / bourgmestre (répond en séance)
    return "conseiller"       # conseiller·ère (dépose des points)


def _current_role_of(key: str, n_depose: int, n_repond: int) -> str:
    """Rôle utilisé pour le sélecteur "Tous les rôles" (Par élu·e) :
    priorité aux mandats déclaratifs (`services.people.mandats`, dates
    réelles), qui distinguent p. ex. un·e ancien·ne échevin·e aujourd'hui
    simple conseiller·ère — repli sur l'heuristique d'activité `_role_of`
    quand la personne n'y figure pas (citoyen·ne, archives antérieures à
    1988…)."""
    return current_role(key) or _role_of(n_depose, n_repond)


def _build_name_registry(pv: list, video: list) -> set:
    """Déduit du corpus les paires (prénom, nom) non ambiguës, pour lever
    l'ambiguïté d'ordre dans `_key`. Signal fiable : une mention à deux
    mots où un seul est tout en majuscules (« Georges VERZIN » → prénom
    Georges, nom de famille VERZIN, comme dans les PV officiels)."""
    pairs: set = set()

    def add_from(raw):
        toks = [t for t in _clean(raw).split() if t]
        if len(toks) == 2:
            caps = [t.isupper() and len(t) > 1 for t in toks]
            if sum(caps) == 1:
                idx = caps.index(True)
                sn, fn = _norm_tok(toks[idx]), _norm_tok(toks[1 - idx])
                pairs.add((fn, sn))

    for s in pv:
        meta = s.get("seance", {}) or {}
        for p in s.get("points", []):
            for interv in (p.get("intervenants") or []):
                add_from(interv)
            for name in _respondents(p.get("repondant") or "", meta):
                add_from(name)
            author = _author_of(p)
            if author:
                add_from(author)
    for s in video:
        for p in s.get("points", []):
            author = p.get("auteur")
            if author and not _is_non_person_video_author(author):
                add_from(author)
    return pairs


def _build_all():
    """Construit l'index par personne + le registre de noms (pairs) + la map
    clé→nom canonique, en une seule passe sur les deux fichiers sources —
    partagés par plusieurs vues (/elu/{key} ET /seance/{date}) sans recalcul."""
    pv = load_db().get("seances", [])
    video = _load_video()
    pairs = _build_name_registry(pv, video)
    people = defaultdict(lambda: {"variants": defaultdict(int), "depose": [], "repond": []})

    def add_variant(k, name):
        people[k]["variants"][_clean(name)] += 1

    pdf_by_date = {}
    session_map = video_session_map()
    # (clé personne, date) -> entrées PV « déposées » de ce jour-là, pour
    # retrouver depuis la boucle vidéo (ci-dessous) le point PV correspondant
    # à un point de chapitrage vidéo, et les fusionner en une seule
    # intervention (voir _match_pv_point).
    pv_lookup = defaultdict(list)

    for s in pv:
        meta = s.get("seance", {}) or {}
        date = meta.get("date")
        pdf_by_date[date] = meta.get("source_url")
        for p in s.get("points", []):
            author, author_key = _point_author(p, pairs, date)
            if author_key:
                add_variant(author_key, author)

            # Noms des répondant·e·s résolus une seule fois (rôle seul type
            # « Bourgmestre » déjà traduit en nom via _respondents) et
            # réutilisés à la fois pour l'agrégation "repond" et pour
            # l'affichage "repondant" côté "depose" — canonisés en fin de
            # fonction (voir nom_by_key) pour un affichage homogène (casse,
            # ordre prénom/nom, nom complet) plutôt que le texte brut du PV.
            resp_raw = p.get("repondant")
            resp_keys = []
            for name in _respondents(resp_raw, meta):
                k = _key(name, pairs)
                if not k:
                    continue
                add_variant(k, name)
                resp_keys.append(k)
                people[k]["repond"].append({
                    "date": date,
                    "sp": p.get("sp") or 0,
                    "titre": p.get("titre") or "",
                    "thematiques": [_thematique_label(t) for t in (p.get("thematiques") or [])],
                    "url": meta.get("source_url"),
                    "demandeur_keys": [author_key] if author_key else [],
                })

            if author_key:
                entry = {
                    "date": date,
                    "sp": p.get("sp") or 0,
                    "type": p.get("type"),
                    "titre": p.get("titre") or "",
                    "thematiques": [_thematique_label(t) for t in (p.get("thematiques") or [])],
                    "repondant_keys": resp_keys,
                    # Repli si le rôle mentionné n'a pas pu être résolu en
                    # nom de personne (ex. « Secrétaire communal », « Président ») :
                    # au moins une casse homogène plutôt que le texte brut du PV.
                    "repondant_fallback": None if resp_keys else (_titlecase(_clean(resp_raw or "")) or None),
                    "url": meta.get("source_url"),
                    "video_url": session_map.get(date),
                }
                people[author_key]["depose"].append(entry)
                pv_lookup[(author_key, date)].append(entry)

    # Questions écrites : canal totalement séparé des points de PV (adressées
    # au Collège hors séance, jamais de SP — voir services/questions_ecrites*.py).
    # Comptées comme une activité "déposée" de plus, au même titre qu'une
    # question orale/demande/motion. Le/la répondant·e (quand nommé·e dans le
    # document — voir la pipeline d'extraction) alimente aussi sa propre
    # activité "repond", comme pour un point de PV.
    for q in load_qe_db().get("questions", []):
        author_raw = q.get("auteur")
        if not author_raw:
            continue
        # Une question écrite peut être cosignée (« Georges Verzin et Cédric
        # Mahieu ») : à la différence d'un point de PV (un seul auteur
        # principal crédité, voir _point_author) — un cosignataire d'une QE
        # n'est jamais un artefact de mise en page, c'est une signature au
        # même titre que la première. Chaque personne nommée obtient donc sa
        # propre entrée "depose".
        author_keys = []
        for name in _split_person_names(author_raw):
            k = _key(name, pairs)
            if not k:
                continue
            add_variant(k, name)
            author_keys.append(k)
        if not author_keys:
            continue

        resp_raw = q.get("repondant")
        resp_key = None
        if resp_raw:
            last = resp_raw.split()[-1] if resp_raw.split() else ""
            if last and not _is_role_token(last):
                resp_key = _key(resp_raw, pairs)
        if resp_key:
            add_variant(resp_key, resp_raw)
            people[resp_key]["repond"].append({
                "date": q.get("date"),
                "sp": 0,
                "titre": q.get("titre") or "",
                "thematiques": [_thematique_label(t) for t in (q.get("thematiques") or [])],
                "url": q.get("source_url"),
                "demandeur_keys": author_keys,
            })

        for author_key in author_keys:
            people[author_key]["depose"].append({
                "date": q.get("date"),
                "sp": 0,
                "type": "question_ecrite",
                "titre": q.get("titre") or "",
                "thematiques": [_thematique_label(t) for t in (q.get("thematiques") or [])],
                "reponse": q.get("reponse"),
                "repondant_keys": [resp_key] if resp_key else [],
                "repondant_fallback": _titlecase(_clean(resp_raw)) if resp_raw else None,
                # Les AUTRES cosignataires de CETTE entrée (jamais soi-même)
                # — résolus en noms canoniques en fin de fonction, pour
                # affichage côte à côte quelle que soit la fiche consultée
                # (voir nom_by_key).
                "co_auteurs_keys": [k for k in author_keys if k != author_key],
                "url": q.get("source_url"),
                "video_url": None,
            })

    for s in video:
        date = s.get("date")
        for p in s.get("points", []):
            author = p.get("auteur")
            if not author or _is_non_person_video_author(author):
                continue
            k = _key(author, pairs)
            if not k:
                continue
            add_variant(k, author)
            titre = p.get("titre_fr") or p.get("titre") or ""
            deeplink = p.get("deeplink")
            candidates = pv_lookup.get((k, date))
            match = _match_pv_point(titre, candidates) if candidates else None
            if match is not None:
                # Même point que ce point PV (même personne, même date, même
                # sujet) : une seule intervention, avec le lien vidéo précis
                # (l'instant exact du point) plutôt que le lien de séance
                # générique — au lieu d'une 2e entrée « Débat filmé » pour
                # la même chose.
                if deeplink:
                    match["video_url"] = deeplink
                    match["video_precise"] = True
                continue
            people[k]["depose"].append({
                "date": date,
                "sp": 0,
                "type": "video",
                "titre": titre,
                # Pas de thématiques pour un débat filmé sans point PV apparié
                # (chapitrage vidéo seul, sans champ `thematiques` source).
                "thematiques": [],
                "repondant": None,
                "url": deeplink,
                "video_url": s.get("video_url"),
            })

    # Enrichissement du nom d'affichage UNIQUEMENT : certain·e·s élu·e·s
    # n'apparaissent en tant qu'auteur·e/répondant·e (les deux seuls rôles
    # comptabilisés) que sous leur seul nom de famille (ex. « M. Denys » en
    # répondant), alors qu'un prénom complet existe dans la liste des
    # intervenant·e·s du même point. On récupère ces variantes plus
    # complètes pour l'affichage, sans jamais créer de fiche ni compter une
    # intervention supplémentaire (une entrée en intervenant·e n'est pas en
    # soi une intervention attribuable, cf. docstring du module).
    for s in pv:
        for p in s.get("points", []):
            for interv in (p.get("intervenants") or []):
                # Une entrée d'intervenant·e peut elle-même être composée
                # (« MM. Bouhjar et El Arnouki ») ou porter un rôle collé au
                # nom (« Bourgmestre Audrey Henry ») : on la découpe comme
                # n'importe quelle autre mention brute avant utilisation,
                # jamais telle quelle (sinon le fragment de rôle/la mention
                # à plusieurs personnes pollue le nom affiché de qui que ce
                # soit dont la clé correspond au dernier mot).
                for name in _split_person_names(interv):
                    k = _key(name, pairs)
                    if k in people:
                        add_variant(k, name)

    # Noms canoniques calculés UNE FOIS que toutes les variantes (dépôts,
    # réponses, enrichissement ci-dessus) sont connues pour tout le monde —
    # réutilisés ensuite pour résoudre "repondant"/"demandeur" en noms
    # complets et homogènes (même casse/ordre que la fiche de la personne),
    # plutôt que le texte brut du PV (ex. « ERALY » ou « VANHALEWYN VINCENT »).
    # Noms d'affichage : lexique éditable prioritaire, puis surcharges en dur,
    # puis meilleure variante observée.
    _lex_noms = lexique_store.person_names()
    nom_by_key = {
        k: _lex_noms.get(k) or _DISPLAY_NAME_OVERRIDES.get(k) or _titlecase(_best_display_variant(d["variants"], k))
        for k, d in people.items()
    }

    index = {}
    for k, d in people.items():
        for entry in d["depose"]:
            keys = entry.pop("repondant_keys", None)
            fallback = entry.pop("repondant_fallback", None)
            if keys is not None:
                names = list(dict.fromkeys(nom_by_key[kk] for kk in keys if kk in nom_by_key))
                entry["repondant"] = " et ".join(names) if names else fallback
            co_keys = entry.pop("co_auteurs_keys", None)
            if co_keys:
                names = list(dict.fromkeys(nom_by_key[kk] for kk in co_keys if kk in nom_by_key))
                entry["co_auteurs"] = " et ".join(names) if names else None
        for entry in d["repond"]:
            dks = entry.pop("demandeur_keys", None) or []
            names = list(dict.fromkeys(nom_by_key[dk] for dk in dks if dk in nom_by_key))
            entry["demandeur"] = " et ".join(names) if names else None
        d["depose"].sort(key=lambda it: (it["date"] or "", it["sp"]), reverse=True)
        d["repond"].sort(key=lambda it: (it["date"] or "", it["sp"]), reverse=True)
        index[k] = {
            "key": k,
            "nom": nom_by_key[k],
            "role": _current_role_of(k, len(d["depose"]), len(d["repond"])),
            "depose": d["depose"],
            "repond": d["repond"],
        }
    return index, pairs, nom_by_key


# ── Cache par mtime des deux fichiers sources ───────────────────────────────
_cache = {"sig": None, "index": None, "pairs": None, "nom_by_key": None}


def _ensure_cache():
    sig = _sig()
    if _cache["sig"] != sig:
        index, pairs, nom_by_key = _build_all()
        _cache["sig"] = sig
        _cache["index"] = index
        _cache["pairs"] = pairs
        _cache["nom_by_key"] = nom_by_key


def _index() -> dict:
    _ensure_cache()
    return _cache["index"]


def _pairs() -> set:
    _ensure_cache()
    return _cache["pairs"]


def _nom_by_key() -> dict:
    _ensure_cache()
    return _cache["nom_by_key"]
