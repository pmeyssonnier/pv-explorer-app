"""
╔══════════════════════════════════════════════════════════════════════════╗
║  BACKFILL DE `source_url` — CORRIGER LE DOUBLE ENCODAGE PERCENT           ║
╚══════════════════════════════════════════════════════════════════════════╝

12 séances (toutes 2010–2011) portent un `source_url` encodé DEUX FOIS —
ex. « Pv%2520CC%25202011-12-21%2520SP.pdf » au lieu de
« Pv%20CC%202011-12-21%20SP.pdf ». `%2520` est le pourcent-encodage de
`%20` lui-même : un espace encodé, puis ré-encodé par erreur (source
inconnue — antérieure à ce dépôt, jamais un geste de ce pipeline).

Conséquence concrète : le lien « PV (PDF) » de ces 12 séances pointe vers
une URL invalide — aucun fichier ne s'appelle littéralement
« Pv%2520CC%2520...pdf » sur 1030.be. Détecté en creusant pourquoi l'ancre
`#page=N` (voir renderPvPdfLink) n'ouvrait pas à la bonne page sur l'une
d'elles : l'URL elle-même était déjà cassée, indépendamment de l'ancre.

Ne touche QUE `seance.source_url` (vérifié : le seul champ concerné dans
toute la base — `models.api`/`index_pv.py` n'incluent jamais l'URL dans le
texte vectorisé, donc AUCUNE réindexation Pinecone requise après ce script).

USAGE (depuis backend/) :
    python backfill_source_url_encoding.py --dry-run   # aperçu, rien écrit
    python backfill_source_url_encoding.py             # applique + réécrit le JSON
"""
import argparse
import json
import os
from urllib.parse import unquote

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pv_conseil_schaerbeek.json")


def backfill_source_url_encoding(db: dict) -> list[dict]:
    """Corrige EN PLACE tout `source_url` double-encodé (repérable à `%25`,
    l'encodage d'un `%` lui-même — jamais présent dans une URL 1030.be saine).
    Idempotent : un `source_url` déjà correct (sans `%25`) n'est pas touché.

    Retourne les séances MODIFIÉES (date, avant, après)."""
    changes = []
    for s in db.get("seances", []):
        meta = s.get("seance") or {}
        url = meta.get("source_url") or ""
        if "%25" not in url:
            continue                                  # déjà correct
        fixed = unquote(url)
        meta["source_url"] = fixed
        changes.append({"date": meta.get("date"), "avant": url, "apres": fixed})
    return changes


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Corrige les source_url doublement pourcent-encodés (%2520 → %20).")
    ap.add_argument("--path", default=_DEFAULT_PATH, help="Base JSON des PV (défaut : dépôt).")
    ap.add_argument("--dry-run", action="store_true", help="Montre le bilan sans écrire.")
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as f:
        db = json.load(f)

    changes = backfill_source_url_encoding(db)

    print(f"{len(changes)} séance(s) avec un source_url corrigé\n")
    for c in changes:
        print(f"  {c['date']} :\n     avant : {c['avant']}\n     après : {c['apres']}")

    if not changes:
        print("Rien à faire (base déjà correcte).")
        return
    if args.dry_run:
        print("\n(dry-run : aucune écriture)")
        return
    with open(args.path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)   # même format que save_database
    print(f"\n→ base réécrite : {args.path}")


if __name__ == "__main__":
    main()
