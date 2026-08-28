"""Mandats électifs déclaratifs (conseiller·ère communal·e / échevin·e /
bourgmestre) par élu·e, avec dates — pour classer le rôle EXACT à la date
d'un point de PV/d'une question écrite. Diffère de `_role_of()`
(services.people.registry), qui dérive un rôle DOMINANT unique par personne
depuis l'activité observée (compteurs dépôt/réponse), sans aucune notion de
date : un·e élu·e ayant occupé successivement plusieurs rôles (ex. Cédric
Mahieu, conseiller depuis 2012 puis aussi échevin depuis 2025) doit être
classé différemment selon la date consultée — c'est ce que fournit
`role_at()` ci-dessous, `_role_of()` restant un repli pour les personnes/
dates hors de ces données déclaratives (ex. citoyen·ne, séance antérieure à
1988 non couverte).

Source : fichier JSON déclaratif édité à la main (`elus_mandats.json`, à la
racine backend/), une entrée par personne. Chaque champ de mandat est soit
null, soit une chaîne "AAAA-AAAA" (mandat clos) ou "AAAA-présent" (mandat en
cours), plusieurs plages séparées par des virgules étant possibles (ex.
"2012-2019, 2024-présent"), avec une annotation entre parenthèses ignorée
pour la classification (« faisant fonction », « en titre »…).

Mise à jour pour une nouvelle législature (ex. 2030) : aucun code à changer,
il suffit d'éditer ce JSON — clore les mandats "-présent" arrivés à échéance
et ajouter les nouveaux/nouvelles élu·e·s avec leurs propres dates
("2030-présent" etc.) ; une plage ouverte couvre automatiquement toute date
future tant qu'elle n'est pas explicitement close.
"""
import datetime
import json
import os
import re

from services.people.names import _key

_MANDATS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "elus_mandats.json"
)

# "2018-2024", "2025-présent", éventuellement suivi d'une annotation entre
# parenthèses ignorée pour la classification (« faisant fonction », « en
# titre », « empêchée en 2026 »… ne changent pas conseiller·ère/Collège).
_RANGE_RE = re.compile(r"^\s*(\d{4})\s*-\s*(\d{4}|pr[ée]sent)\s*(?:\(.*\))?\s*$", re.I)


def _parse_ranges(raw) -> list:
    """« 2012-2018, 2024-présent » -> [(2012, 2018), (2024, None)] (None =
    mandat en cours, borne supérieure ouverte). None/vide -> []. Un segment
    non reconnu est ignoré silencieusement (donnée déclarative externe,
    jamais d'exception sur une ligne malformée)."""
    if not raw:
        return []
    out = []
    for part in raw.split(","):
        m = _RANGE_RE.match(part)
        if not m:
            continue
        start = int(m.group(1))
        end_raw = m.group(2).lower()
        end = None if end_raw.startswith("pr") else int(end_raw)
        out.append((start, end))
    return out


def _load_mandats_raw() -> list:
    try:
        with open(_MANDATS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    return data if isinstance(data, list) else []


_cache = {"mtime": None, "by_key": None}


def _mandats_by_key() -> dict:
    """{clé_personne (voir services.people.names._key) : {"conseiller": [...],
    "echevin": [...], "bourgmestre": [...]}} — mis en cache par mtime du
    fichier source (comme les autres bases JSON du projet)."""
    try:
        mtime = os.path.getmtime(_MANDATS_PATH)
    except OSError:
        mtime = None
    if _cache["mtime"] != mtime:
        by_key = {}
        for e in _load_mandats_raw():
            nom = e.get("nom")
            if not nom:
                continue
            k = _key(nom)
            if not k:
                continue
            by_key[k] = {
                "conseiller": _parse_ranges(e.get("conseiller_communal")),
                "echevin": _parse_ranges(e.get("echevin")),
                "bourgmestre": _parse_ranges(e.get("bourgmestre")),
            }
        _cache["mtime"] = mtime
        _cache["by_key"] = by_key
    return _cache["by_key"] or {}


def _year_in_ranges(year: int, ranges: list) -> bool:
    return any(start <= year and (end is None or year <= end) for start, end in ranges)


def role_at(key: str, date) -> str | None:
    """Rôle EXACT à la date donnée (ISO "AAAA-MM-JJ", ou juste une année) :
    "college" (échevin·e/bourgmestre), "conseiller", ou None si la personne
    est absente de ces mandats déclaratifs, ou si la date tombe hors de tout
    mandat connu pour elle (ex. citoyen·ne sans mandat, période antérieure à
    l'entrée en fonction, trou entre deux mandats non consécutifs). Un
    mandat Collège l'emporte sur un mandat conseiller·ère simultané — les
    échevin·e·s/le bourgmestre restent conseiller·ère·s, mais répondent en
    séance ès qualité de Collège, jamais l'inverse."""
    if not key or not date:
        return None
    m = _mandats_by_key().get(key)
    if not m:
        return None
    try:
        year = int(str(date)[:4])
    except (ValueError, TypeError):
        return None
    if _year_in_ranges(year, m["echevin"]) or _year_in_ranges(year, m["bourgmestre"]):
        return "college"
    if _year_in_ranges(year, m["conseiller"]):
        return "conseiller"
    return None


def current_role(key: str) -> str | None:
    """Rôle à la date du jour — sert de repli plus précis que `_role_of()`
    (compteurs d'activité) pour classer une personne dans le sélecteur
    « Tous les rôles » de l'onglet Par élu·e, quand ses mandats sont connus."""
    return role_at(key, datetime.date.today().isoformat())


def mandats_for(key: str) -> dict | None:
    """Mandats structurés d'une personne, sous forme de plages
    {"debut": int, "fin": int|None} par type — pour l'affichage détaillé de
    l'historique de mandats dans sa fiche (onglet Par élu·e). None si la
    personne est absente de ces données déclaratives."""
    m = _mandats_by_key().get(key)
    if not m:
        return None
    return {
        kind: [{"debut": start, "fin": end} for start, end in ranges]
        for kind, ranges in m.items() if ranges
    } or None


# ── Édition (panneau admin) ─────────────────────────────────────────────────
# Session #hfkq92 : sous-onglet admin pour visualiser/corriger les mandats
# déclaratifs sans passer par un commit manuel — ce module ne servait jusqu'ici
# qu'en lecture (voir docstring en tête de fichier).
def list_mandats() -> list:
    """Liste brute des entrées (voir _load_mandats_raw) pour l'admin — pas de
    cache mtime ici, fichier de quelques dizaines de Ko, toujours à jour."""
    return _load_mandats_raw()


def _validate_range(raw, champ: str) -> None:
    """Lève ValueError si `raw` contient un segment qui ne matche pas
    _RANGE_RE — un segment mal formé est sinon silencieusement ignoré par
    _parse_ranges, ce qui masquerait une faute de frappe de l'admin plutôt
    que de la signaler tout de suite."""
    if not raw:
        return
    for part in raw.split(","):
        if not _RANGE_RE.match(part):
            raise ValueError(
                f"{champ} : segment invalide « {part.strip()} » "
                "(attendu « AAAA-AAAA » ou « AAAA-présent », virgule pour plusieurs plages)"
            )


def save_mandat(nom: str, conseiller_communal, echevin, bourgmestre, statut,
                 nom_original: str | None = None) -> dict:
    """Ajoute ou met à jour (par nom) une entrée de elus_mandats.json, l'écrit
    sur le disque local (effet immédiat sur l'instance courante — même
    mécanique que lexique_store.add_entry) et retourne l'entrée sauvegardée.
    `nom_original` : nom AVANT modification, pour retrouver l'entrée même si
    l'admin corrige aussi le nom lui-même (sinon une simple correspondance
    sur `nom` créerait un doublon au lieu de renommer). Le commit dans le
    dépôt (persistance/redéploiement) est fait par l'appelant (endpoint
    admin, via services.github_publish). Lève ValueError si le nom est vide
    ou une plage mal formée."""
    nom = (nom or "").strip()
    if not nom:
        raise ValueError("nom requis")
    _validate_range(conseiller_communal, "conseiller_communal")
    _validate_range(echevin, "echevin")
    _validate_range(bourgmestre, "bourgmestre")

    data = _load_mandats_raw()
    entry = {
        "nom": nom,
        "conseiller_communal": (conseiller_communal or "").strip() or None,
        "echevin": (echevin or "").strip() or None,
        "bourgmestre": (bourgmestre or "").strip() or None,
        "statut": (statut or "").strip() or None,
    }
    cherche = (nom_original or nom).strip()
    idx = next((i for i, e in enumerate(data) if e.get("nom") == cherche), None)
    if idx is not None:
        data[idx] = entry
    else:
        data.append(entry)

    with open(_MANDATS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return entry


def as_json(data: list | None = None) -> str:
    return json.dumps(data if data is not None else _load_mandats_raw(), ensure_ascii=False, indent=2) + "\n"
