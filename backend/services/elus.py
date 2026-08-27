"""Agrégation « Interventions par élu·e » : recherche STRUCTURÉE et exhaustive,
lue depuis la base JSON des PV (+ chapitrage vidéo), sans embedding ni Pinecone.

Motivation : la recherche sémantique du chat (/ask) est sensible à la
formulation (« Georges Verzin » vs « Verzin ») et non exhaustive. Ici on
agrège de façon déterministe l'ensemble des interventions d'une personne.

Fonctions pures + cache mémoire par mtime (les fichiers ne changent qu'au
redéploiement). Les consommateurs ne font que LIRE le dict → partage sûr.

La logique métier vit dans des modules dédiés par sous-domaine (le nombre de
correctifs ponctuels sur la résolution des personnes — coquilles, alias,
Bourgmestre f.f., organismes captés comme personnes... — indiquait un
sous-domaine à part entière) :
  - services.people.names        : normalisation des noms, clés d'identité
  - services.people.attribution  : qui a déposé/répondu à un point
  - services.people.registry     : index par personne + cache
  - services.video_merge         : fusion PV ↔ chapitre vidéo
  - services.seances             : agrégation par séance (onglet « Séances »)

Ce module reste le point d'entrée historique : il expose l'agrégation par
élu·e (`elus_list`/`elu_detail`) et réexporte les symboles des modules
ci-dessus pour ne rien casser côté appelant·e·s (routeur, tests)."""
from services.statistics import load_db  # noqa: F401 (réexporté)

from services.people.attribution import (  # noqa: F401 (réexportés)
    _AUTHOR_IN_TITLE, _MANUAL_AUTHOR_OVERRIDES, _TYPE_LABEL, _author_of,
    _first_named_intervenant, _point_author, _respondents,
)
from services.people.names import (  # noqa: F401 (réexportés)
    _best_display_variant, _clean, _HOMONYM_KEY_OVERRIDES, _is_non_person_video_author,
    _is_role_token, _key, _norm_tok, _resolve_display_name, _split_person_names, _titlecase,
)
from services.people.mandats import mandats_for  # noqa: F401 (réexporté)
from services.people.registry import (  # noqa: F401 (réexportés)
    _build_all, _build_name_registry, _ensure_cache, _index, _load_video,
    _nom_by_key, _pairs, _role_of, _sig,
)
from services.video_merge import _match_pv_point  # noqa: F401 (réexporté)
from services.seances import (  # noqa: F401 (réexportés)
    _decision_summary, _is_reportee, _is_retire, _thematique_label, seance_detail, seances_list,
)

# ── API publique (consommée par le routeur) ─────────────────────────────────
# Seuil d'affichage : écarte les artefacts d'extraction (une seule apparition).
_MIN_TOTAL = 2


def elus_list() -> list:
    """Liste des élu·e·s avec compteurs et rôle, triée par activité décroissante.
    Filtre les personnes à activité négligeable (< _MIN_TOTAL interventions)."""
    out = []
    for e in _index().values():
        nd, nr = len(e["depose"]), len(e["repond"])
        if nd + nr < _MIN_TOTAL:
            continue
        out.append({
            "key": e["key"],
            "nom": e["nom"],
            "role": e["role"],
            "depose": nd,
            "repond": nr,
        })
    out.sort(key=lambda x: (x["depose"] + x["repond"], x["depose"]), reverse=True)
    return out


def elu_detail(key: str):
    """Détail d'un·e élu·e (déposé + réponses) ou None si inconnu."""
    e = _index().get((key or "").strip().lower())
    if not e:
        return None

    def _fmt_depose(it):
        return {
            "date": it["date"],
            "sp": it["sp"],
            "page": it.get("page"),
            "type_label": _TYPE_LABEL.get(it["type"], "Point"),
            "type": it["type"],
            "titre": it["titre"],
            "thematiques": it.get("thematiques") or [],
            "repondant": it.get("repondant"),
            "reponse": it.get("reponse"),
            "co_auteurs": it.get("co_auteurs"),
            "url": it.get("url"),
            "video_url": it.get("video_url"),
            "video_precise": bool(it.get("video_precise")),
            # Même résumé d'issue que l'onglet Séances pour ce point (None
            # pour un débat filmé/une question écrite, qui n'en ont pas).
            "decision": it.get("decision"),
            "reporte": bool(it.get("reporte")),
            "retire": bool(it.get("retire")),
            "montant_eur": it.get("montant_eur"),
        }

    def _fmt_repond(it):
        return {
            "date": it["date"],
            "sp": it["sp"],
            "page": it.get("page"),
            "type_label": _TYPE_LABEL.get(it.get("type"), "Point"),
            "titre": it["titre"],
            "thematiques": it.get("thematiques") or [],
            "url": it.get("url"),
            "demandeur": it.get("demandeur"),
            # Les autres répondant·e·s de CE point (jamais soi-même) : un
            # point à plusieurs répondant·e·s montre les deux, comme l'onglet
            # Séances — jamais escamotés derrière le seul nom de la fiche.
            "co_repondants": it.get("co_repondants"),
            # Point délibératif seulement (voir registry.py) : qui d'autre
            # est intervenu au débat, et ce que le conseil en a décidé.
            "intervenants": it.get("intervenants"),
            "decision": it.get("decision"),
            "reporte": bool(it.get("reporte")),
            "retire": bool(it.get("retire")),
            "montant_eur": it.get("montant_eur"),
        }

    depose = e["depose"]
    n_q = sum(1 for it in depose if it["type"] == "question_orale")
    n_d = sum(1 for it in depose if it["type"] == "demande_habitant")
    n_m = sum(1 for it in depose if it["type"] == "motion")
    n_v = sum(1 for it in depose if it["type"] == "video")
    return {
        "key": e["key"],
        "nom": e["nom"],
        "role": e["role"],
        # Historique de mandats déclaratif (voir services.people.mandats),
        # None si la personne n'y figure pas — le frontend retombe alors sur
        # le libellé "role" simple (voir elus.js/renderElu).
        "mandats": mandats_for(e["key"]),
        "counts": {
            "depose": len(depose),
            "repond": len(e["repond"]),
            "questions": n_q,
            "demandes": n_d,
            "motions": n_m,
            "videos": n_v,
        },
        "depose": [_fmt_depose(it) for it in depose],
        "repond": [_fmt_repond(it) for it in e["repond"]],
    }
