"""
╔══════════════════════════════════════════════════════════════════════════╗
║  SÉPARATION STATUT / DÉCISION / DÉBAT — DÉRIVÉE, SANS PDF NI LLM         ║
╚══════════════════════════════════════════════════════════════════════════╝

Le champ `decision` d'un point mélange aujourd'hui TROIS dimensions que le
procès-verbal, lui, distingue :

    ce qu'est le point   →  `type`               (déjà séparé)
    comment il a été traité  →  reporté / retiré / traité
    ce qu'il est devenu  →  approuvé, décidé, pris pour information…

Un point « REPORTÉ » n'a pas été décidé : il a été renvoyé à une séance
ultérieure. Un point « DÉBAT » n'a rien décidé non plus : il a été discuté.
Sur 10 062 points, 1 480 (14,7 %) portent ainsi une « décision » qui n'en est
pas une — 651 statuts de traitement et 829 débats.

Cette confusion a une conséquence visible : une question orale répondue mais
non tranchée apparaît comme un point « sans issue relevée », alors que ne rien
décider est son régime NORMAL (le pipeline le sait déjà — voir
audit_completeness.TYPES_SANS_DECISION — mais le modèle de données ne le dit
pas).

Ce script sépare les trois dimensions SANS ré-extraction : le mot écrit dans
`decision` suffit à savoir dans quelle case il va, pour 100 % du corpus.

    REPORTÉ / RETIRÉ  →  statut_traitement, decision effacée   (651 points)
    DÉBAT             →  debat = true,      decision effacée   (829 points)
    tout le reste     →  statut_traitement = "traité", decision inchangée

Ré-extraire les 170 PV coûterait des appels Claude et une réindexation
complète pour récupérer une information DÉJÀ présente. Seule une information
réellement nouvelle — le rejet d'une motion, un amendement — demanderait de
relire les PDF ; c'est le rôle de reextract_targeted.

POURQUOI `statut_traitement` ET NON `statut`
    `statut` est déjà le nom d'un champ DÉRIVÉ côté API (services.seances :
    le libellé canonique de la décision, qui alimente les puces de l'onglet
    Séances). Deux sens pour un mot, l'un stocké et l'autre calculé, se
    paieraient à chaque relecture. Le champ stocké porte donc un nom qui dit
    ce qu'il est : le statut du TRAITEMENT du point.

CE QUE `debat` NE DIT PAS
    `debat: true` signifie que le PV a enregistré « DÉBAT » comme issue du
    point. `false` ne signifie pas qu'il n'y a pas eu de discussion — un point
    approuvé après débat porte « APPROUVÉ », et le PV ne garde alors aucune
    trace de la discussion dans ce champ. On ne l'infère pas depuis
    `intervenants` : ce serait déduire un champ d'un autre, précisément ce que
    la séparation cherche à éviter.

USAGE (dépôt) :
    python pipeline/split_statut_decision.py --dry-run   # aperçu, rien écrit
    python pipeline/split_statut_decision.py             # applique + réécrit
    python pipeline/split_statut_decision.py --path <chemin>

⚠️ N'APPLIQUER QU'APRÈS avoir migré les vues qui lisent `decision` : les puces
   de statut de l'onglet Séances, les deux graphes de l'onglet Statistiques et
   l'identité « points = somme des issues + solde », _is_reportee/_is_retire.
   Effacer `decision` sur 1 480 points avant elles casserait l'affichage.
"""
import argparse
import json
import os
from collections import Counter

from utils_statut import STATUTS_TRAITEMENT, classer_decision

# Chemin par défaut : la base versionnée du dépôt (pipeline/ ↔ backend/).
_DEFAULT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "backend", "pv_conseil_schaerbeek.json",
)


def split_statut_decision(db: dict) -> list[dict]:
    """Sépare EN PLACE statut de traitement, débat et décision.

    Retourne la liste des points MODIFIÉS (date, sp, avant, après). Idempotent :
    relancé sur une base déjà séparée, il ne change rien — un point qui porte
    déjà `statut_traitement` est laissé tel quel.
    """
    changes = []
    for s in db.get("seances", []):
        date = (s.get("seance") or {}).get("date")
        for p in s.get("points", []):
            if "statut_traitement" in p:
                continue                      # déjà séparé
            avant = p.get("decision")
            statut, decision, debat = classer_decision(avant)
            p["statut_traitement"] = statut
            p["debat"] = debat
            p["decision"] = decision
            if decision != avant or debat:
                changes.append({
                    "date": date, "sp": p.get("sp"), "type": p.get("type"),
                    "avant": avant, "statut_traitement": statut, "debat": debat,
                    "decision": decision,
                })
    return changes


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Sépare statut de traitement / débat / décision (déterministe, sans LLM).")
    ap.add_argument("--path", default=_DEFAULT_PATH, help="Base JSON des PV (défaut : dépôt).")
    ap.add_argument("--dry-run", action="store_true", help="Montre le bilan sans écrire.")
    ap.add_argument("--exemples", type=int, default=8, help="Nombre d'exemples affichés par cas.")
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as f:
        db = json.load(f)

    total = sum(len(s.get("points") or []) for s in db.get("seances", []))
    changes = split_statut_decision(db)

    par_cas = Counter(
        c["statut_traitement"] if c["statut_traitement"] != "traité" else "débat"
        for c in changes
    )
    print(f"Base : {total} points, {len(changes)} déplacés hors de `decision` "
          f"({len(changes) / total:.1%})\n")
    for cas in list(STATUTS_TRAITEMENT - {"traité"}) + ["débat"]:
        exemples = [c for c in changes if
                    (c["statut_traitement"] == cas or (cas == "débat" and c["debat"]))]
        if not exemples:
            continue
        print(f"  {cas.upper()} — {par_cas[cas]} points, ex. :")
        for c in exemples[:args.exemples]:
            print(f"     {c['date']} SP{c['sp']:<4} [{c['type']}]  « {c['avant']} » → "
                  f"statut={c['statut_traitement']}, debat={str(c['debat']).lower()}, decision=null")
        print()

    if not changes:
        print("Rien à faire (base déjà séparée).")
        return
    if args.dry_run:
        print("(dry-run : aucune écriture)")
        return
    with open(args.path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)   # même format que save_database
    print(f"→ base réécrite : {args.path}")


if __name__ == "__main__":
    main()
