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
