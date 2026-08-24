"""Lexique éditable : synonymes/associations + glossaire, stockés dans
`backend/lexique.json` et enrichissables à chaud (voir routers/admin.py, commande
« //lex … » réservée à l'admin — écrit le fichier localement pour effet immédiat
sur l'instance courante, puis le commite dans le dépôt pour persistance/redéploiement).

Cinq sections :
  • thematiques : {variante_canonisée: forme_canonique}   (fusion de tags)
  • decisions   : {texte_sans_accents_minuscule: Libellé}  (synonymes d'affichage)
  • personnes   : {"alias": {clé_fautive: clé_correcte},
                   "noms":  {clé: "Nom Affiché"}}
  • extraction  : {"retrait"|"report"|"approbation"|"rejet": [phrases…]}
                  (phrases ajoutées aux regex de la pipeline — futures extractions)
  • glossaire   : {terme: définition}                      (comprehension / RAG)

Module RACINE (comme config.py) sans dépendance à utils/ ni services/ : il est
importé par les deux (utils.text, services.people.names…) — aucun cycle possible.
"""
import json
import os

_PATH = os.path.join(os.path.dirname(__file__), "lexique.json")

# Types de commande → (section, sous-clé éventuelle, "map" ou "list").
_KINDS = {
    "theme": ("thematiques", None, "map"),
    "decision": ("decisions", None, "map"),
    "alias": ("personnes", "alias", "map"),
    "nom": ("personnes", "noms", "map"),
    "def": ("glossaire", None, "map"),
    "retrait": ("extraction", "retrait", "list"),
    "report": ("extraction", "report", "list"),
    "approbation": ("extraction", "approbation", "list"),
    "rejet": ("extraction", "rejet", "list"),
}


def _empty() -> dict:
    return {
        "thematiques": {},
        "decisions": {},
        "personnes": {"alias": {}, "noms": {}},
        "extraction": {"retrait": [], "report": [], "approbation": [], "rejet": []},
        "glossaire": {},
    }


_cache = {"mtime": None, "data": None}


def load() -> dict:
    """Lexique courant (cache par mtime). Tolère l'absence/corruption du fichier
    (retourne alors des sections vides) — le lexique est ADDITIF : il complète
    les constantes en dur, jamais un prérequis au démarrage."""
    try:
        mtime = os.path.getmtime(_PATH)
    except OSError:
        mtime = None
    # `data is None` force le 1er chargement même si le fichier est absent
    # (mtime None) — sinon le cache initial (mtime None) masquerait le rechargement.
    if _cache["data"] is None or _cache["mtime"] != mtime:
        data = _empty()
        try:
            with open(_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for k, v in _empty().items():
                    got = raw.get(k)
                    if isinstance(got, type(v)):
                        data[k] = {**v, **got} if isinstance(v, dict) else got
                # personnes : fusion des sous-sections
                pers = raw.get("personnes")
                if isinstance(pers, dict):
                    data["personnes"]["alias"] = {**data["personnes"]["alias"], **(pers.get("alias") or {})}
                    data["personnes"]["noms"] = {**data["personnes"]["noms"], **(pers.get("noms") or {})}
        except Exception:
            pass
        _cache["mtime"] = mtime
        _cache["data"] = data
    return _cache["data"]


# ── Accès par section (lecture) ─────────────────────────────────────────────
def thematiques() -> dict:
    return load()["thematiques"]


def decisions() -> dict:
    return load()["decisions"]


def person_aliases() -> dict:
    return load()["personnes"]["alias"]


def person_names() -> dict:
    return load()["personnes"]["noms"]


def extraction_phrases(famille: str) -> list:
    return load()["extraction"].get(famille, [])


def glossaire() -> dict:
    return load()["glossaire"]


def as_json(data: dict | None = None) -> str:
    return json.dumps(data if data is not None else load(), ensure_ascii=False, indent=2) + "\n"


# ── Mutation (ajout d'une entrée) ───────────────────────────────────────────
def add_entry(kind: str, key: str, value: str) -> dict:
    """Ajoute une entrée au lexique (voir _KINDS), l'écrit sur le disque local
    (effet immédiat sur l'instance courante) et retourne le lexique à jour.
    Le commit dans le dépôt (persistance) est fait par l'appelant (endpoint
    admin, via services.github_publish). Lève ValueError si kind/clé invalide."""
    spec = _KINDS.get(kind)
    if not spec:
        raise ValueError(f"type de lexique inconnu : {kind!r} (attendus : {', '.join(_KINDS)})")
    key = (key or "").strip()
    value = (value or "").strip()
    section, sub, shape = spec
    data = json.loads(json.dumps(load()))  # copie profonde (ne pas muter le cache in place)
    target = data[section][sub] if sub else data[section]
    if shape == "list":
        # Famille d'extraction (retrait/report/…) : une phrase à ajouter, pas de clé.
        if not value:
            raise ValueError("phrase requise")
        if value not in target:
            target.append(value)
    else:
        if not key or not value:
            raise ValueError("clé et valeur requises")
        target[key] = value
    # Écriture locale best-effort + invalidation du cache (mtime).
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            f.write(as_json(data))
        _cache["mtime"] = None  # forcera un rechargement propre au prochain load()
    except OSError:
        _cache["data"] = data   # au moins l'instance courante reflète l'ajout
    return data
