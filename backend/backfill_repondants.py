"""
╔══════════════════════════════════════════════════════════════════════════╗
║  BACKFILL DE `repondants` — REFORMATÉ, PAS DÉRIVÉ, DEPUIS `repondant`     ║
╚══════════════════════════════════════════════════════════════════════════╝

Depuis PR #169, `index_pv.py` (l'indexation Pinecone) et l'API des sources du
chat (`models.api.Source`) ne lisent QUE `repondants` (liste) — jamais l'ancien
champ singulier `repondant`, par principe : « un répondant recopié depuis un
autre champ inventerait un rôle que le PV n'attribue pas ». Tant que la base
n'a pas été RÉ-EXTRAITE avec le nouveau schéma, `repondants` reste absent
partout, et les sources du chat n'affichent donc aucun·e répondant·e — même
sur les 2 171 points où le PV en nomme un·e.

CE SCRIPT N'EST PAS UNE RÉ-EXTRACTION. `repondant` (singulier, parfois composé
— « Cédric Mahieu et Justine Harzé ») porte déjà l'information ; il suffit de
la reformater en liste. C'est ce que fait déjà, EN DIRECT et à chaque requête,
`services.people.attribution._respondents()` — utilisée par `services.seances`
pour afficher les répondant·e·s de l'onglet Séances. Ce script APPELLE cette
même fonction et écrit son résultat dans `repondants`, une fois pour toutes :
aucune nouvelle logique, aucun texte relu qui ne l'était pas déjà.

Ce n'est PAS le même geste que pour `auteurs` : l'attribution d'auteur (titre/
résumé) s'appuie sur une heuristique délibérément prudente (`_author_of`),
avec une vingtaine d'exceptions vérifiées à la main pour des cas ambigus —
l'écrire comme si c'était « lu dans le texte » figerait ses angles morts.
`_respondents()` n'a pas cette ambiguïté : elle ne fait que découper une
chaîne déjà validée par l'extraction, sans deviner un nom absent.

`repondant` (singulier) n'est PAS retiré : `services.seances` continue de le
lire pour l'affichage, rien ne casse s'il reste. Seul `repondants` est ajouté.

USAGE (depuis backend/) :
    python backfill_repondants.py --dry-run   # aperçu, rien écrit
    python backfill_repondants.py             # applique + réécrit le JSON

⚠️ APRÈS APPLICATION : le texte vectorisé des points concernés change (une
   ligne « Répondants : … » apparaît). Contrairement à backfill_rejets.py (10
   points), cette fois ~2 000 points sont touchés — pas un upsert ciblé
   anodin. Réindexer avec reindex_points.py --depuis <ref d'avant ce commit>
   quand le budget d'embedding le permet ; rien ne presse, l'app reste
   cohérente avec l'index existant tant que ce n'est pas fait (voir
   utils.statut.dimensions, même principe de double lecture).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.people.attribution import _respondents  # noqa: E402

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pv_conseil_schaerbeek.json")


def backfill_repondants(db: dict) -> list[dict]:
    """Écrit EN PLACE `repondants` (liste) à partir de `repondant` (singulier),
    sur tous les points qui n'ont pas encore le champ pluriel. Idempotent :
    relancé sur une base déjà backfillée, il ne change rien.

    Retourne les points MODIFIÉS (date, sp, avant, après) — y compris ceux où
    le résultat est une liste VIDE (mention de rôle non résolue faute de
    bourgmestre/ff enregistré pour cette séance) : le champ est ajouté quand
    même, pour que « pas de répondant·e trouvé » soit visible plutôt qu'absent."""
    changes = []
    for s in db.get("seances", []):
        meta = s.get("seance") or {}
        date = meta.get("date")
        for p in s.get("points", []):
            if "repondants" in p:
                continue                                  # déjà backfillé
            raw = p.get("repondant")
            if not raw:
                continue                                  # rien à reformater
            noms = _respondents(raw, meta)
            p["repondants"] = noms
            changes.append({"date": date, "sp": p.get("sp"), "avant": raw, "apres": noms})
    return changes


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Reformate `repondant` (singulier) en `repondants` (liste) — sans LLM, sans PDF.")
    ap.add_argument("--path", default=_DEFAULT_PATH, help="Base JSON des PV (défaut : dépôt).")
    ap.add_argument("--dry-run", action="store_true", help="Montre le bilan sans écrire.")
    ap.add_argument("--exemples", type=int, default=10, help="Nombre d'exemples affichés.")
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as f:
        db = json.load(f)

    changes = backfill_repondants(db)
    resolus = [c for c in changes if c["apres"]]
    non_resolus = [c for c in changes if not c["apres"]]

    print(f"{len(changes)} point(s) portant `repondant` reformatés en `repondants`\n")
    print(f"  Résolus  : {len(resolus)}")
    for c in resolus[:args.exemples]:
        print(f"     {c['date']} SP{c['sp']:<5} « {c['avant']} » → {c['apres']}")
    if non_resolus:
        print(f"\n  ⚠ Non résolus (mention de rôle sans bourgmestre/ff enregistré pour la séance) : {len(non_resolus)}")
        for c in non_resolus[:args.exemples]:
            print(f"     {c['date']} SP{c['sp']:<5} « {c['avant']} » → []")

    if not changes:
        print("Rien à faire (base déjà backfillée).")
        return
    if args.dry_run:
        print("\n(dry-run : aucune écriture)")
        return
    with open(args.path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)   # même format que save_database
    print(f"\n→ base réécrite : {args.path}")
    print("⚠ Réindexer Pinecone (reindex_points.py --depuis <ref d'avant ce commit>) quand le budget le permet.")


if __name__ == "__main__":
    main()
