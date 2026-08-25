"""
╔══════════════════════════════════════════════════════════════════════════╗
║  RÉINDEXATION CIBLÉE — QUELQUES POINTS, PAS TOUT L'INDEX                  ║
╚══════════════════════════════════════════════════════════════════════════╝

Une correction de données ne touche parfois que quelques points : les 10
motions inscrites « REJETÉ » (voir pipeline/backfill_rejets.py) ont changé de
texte vectorisé, les 10 052 autres non. `index_pv.py --only-year` aurait
ré-embeddé sept années entières — des milliers de points — pour dix vecteurs.
Ce script n'envoie QUE ceux qu'on lui nomme.

Le quota d'embedding Pinecone est limité (plan Starter : 250 000 tokens/min,
voir index_pv) : ré-embedder ce qui n'a pas bougé se paie sans rien apporter.

DEUX FAÇONS DE DÉSIGNER LES POINTS

    --depuis <ref git>   les points dont le TEXTE VECTORISÉ a changé depuis
                         cette révision de la base. Rien à recopier à la main,
                         et c'est la seule liste qui ne puisse pas mentir :
                         elle est calculée, pas retenue.
    --id <id,id,…>       la liste explicite (ex. PV-2020-10-28_SP139).

L'upsert est idempotent — l'ID d'un point est stable (voir index_pv.
point_to_chunk) — donc relancer ce script ne crée jamais de doublon, et les
vecteurs non nommés ne sont pas touchés.

USAGE (depuis backend/) :
    python reindex_points.py --depuis HEAD~1 --dry-run   # ce qui serait envoyé
    python reindex_points.py --depuis HEAD~1             # envoie
    python reindex_points.py --id PV-2020-10-28_SP139
    python reindex_points.py --id PV-2020-10-28_SP139 --verifier   # relit l'index

`--dry-run` et `--verifier` interrogent l'index en lecture pour COMPARER ce
qu'il contient à ce que la base produit : c'est ce qui dit si la réindexation
est encore nécessaire, ou si elle a bien pris.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from index_pv import (
    DEFAULT_JSON, INDEX_NAME, ensure_index, get_client, index_chunks, load_chunks,
)


def _texte(chunk: dict) -> str:
    return chunk["metadata"].get("chunk_text", "")


def _par_id(chunks: list[dict]) -> dict:
    return {c["id"]: c for c in chunks}


def ids_modifies(chunks_actuels: list[dict], chunks_anciens: list[dict]) -> list[str]:
    """Les ID dont le texte vectorisé DIFFÈRE entre deux états de la base.

    Un point ajouté compte comme modifié (il n'existe pas encore dans l'index) ;
    un point supprimé n'est pas rendu — ce script n'efface rien, et supprimer un
    vecteur est une opération assez différente pour mériter la sienne.
    """
    avant, apres = _par_id(chunks_anciens), _par_id(chunks_actuels)
    return sorted(i for i, c in apres.items()
                  if i not in avant or _texte(avant[i]) != _texte(c))


def chunks_depuis_git(ref: str, json_path: Path, commune: str) -> list[dict]:
    """Les chunks que produirait la base telle qu'elle était à `ref`.

    Passe par `git show` plutôt que par une copie de sauvegarde : la révision
    est nommée sans ambiguïté et rien à ranger ensuite. Le CODE, lui, est celui
    d'aujourd'hui — c'est bien ce qu'on veut : on compare deux états des
    DONNÉES à travers la même moulinette.
    """
    # Le dépôt est celui du FICHIER, pas celui du répertoire courant : le script
    # se lance depuis backend/, mais rien n'oblige --input à y être.
    racine = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                            cwd=json_path.resolve().parent,
                            capture_output=True, text=True, check=True).stdout.strip()
    relatif = os.path.relpath(json_path.resolve(), racine).replace(os.sep, "/")
    blob = subprocess.run(["git", "-C", racine, "show", f"{ref}:{relatif}"],
                          capture_output=True, text=True, check=True).stdout
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8",
                                     delete=False) as f:
        f.write(blob)
        tmp = Path(f.name)
    try:
        return load_chunks(tmp, commune)
    finally:
        tmp.unlink(missing_ok=True)


def _lire_index(pc, ids: list[str]) -> dict:
    """{id: chunk_text} tel que Pinecone le stocke AUJOURD'HUI, pour comparer.

    Best-effort : la forme de la réponse a changé selon les versions du SDK, et
    ne pas pouvoir relire l'index n'est pas une raison de refuser de l'écrire.
    En cas d'échec, on le dit et on continue sans comparaison.
    """
    try:
        rep = pc.Index(INDEX_NAME).fetch(ids=ids, namespace="pv")
        vecteurs = getattr(rep, "vectors", None)
        if vecteurs is None and isinstance(rep, dict):
            vecteurs = rep.get("vectors", {})
        out = {}
        for i, v in (vecteurs or {}).items():
            meta = getattr(v, "metadata", None)
            if meta is None and isinstance(v, dict):
                meta = v.get("metadata")
            out[i] = (meta or {}).get("chunk_text", "")
        return out
    except Exception as e:  # noqa: BLE001 — lecture d'agrément, jamais bloquante
        print(f"⚠️  Index illisible pour comparaison ({type(e).__name__}: {e})")
        return {}


def _ligne_decision(texte: str) -> str:
    for ligne in (texte or "").split("\n"):
        if ligne.startswith("Décision :"):
            return ligne
    return "(pas de ligne Décision)"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Réindexe UNIQUEMENT les points nommés (ou modifiés depuis une révision).")
    ap.add_argument("--input", default=DEFAULT_JSON, help="Base JSON des PV.")
    ap.add_argument("--commune", default="schaerbeek", help="Commune (métadonnée).")
    ap.add_argument("--depuis", default="", metavar="REF",
                    help="Révision git : réindexe ce dont le texte vectorisé a changé depuis.")
    ap.add_argument("--id", default="", help="ID explicites, séparés par des virgules.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Montre ce qui serait envoyé, sans rien écrire.")
    ap.add_argument("--verifier", action="store_true",
                    help="Après l'envoi, relit l'index et confirme que le texte a pris.")
    args = ap.parse_args()

    if bool(args.depuis) == bool(args.id):
        print("❌ Choisir --depuis <ref> OU --id <liste> (exactement l'un des deux).")
        sys.exit(1)

    json_path = Path(args.input)
    if not json_path.exists():
        print(f"❌ Fichier introuvable : {json_path}")
        sys.exit(1)
    commune = args.commune.strip().lower()
    chunks = load_chunks(json_path, commune)
    par_id = _par_id(chunks)

    if args.depuis:
        ids = ids_modifies(chunks, chunks_depuis_git(args.depuis, json_path, commune))
        print(f"🎯 {len(ids)} point(s) dont le texte vectorisé a changé depuis {args.depuis}")
    else:
        ids = [i.strip() for i in args.id.split(",") if i.strip()]
        inconnus = [i for i in ids if i not in par_id]
        if inconnus:
            # Un ID mal recopié passerait sinon inaperçu : rien à envoyer, rien
            # à corriger dans l'index, et un « terminé » trompeur.
            print(f"❌ ID absent(s) de la base : {', '.join(inconnus)}")
            sys.exit(1)

    if not ids:
        print("✅ Rien à réindexer.")
        return

    a_envoyer = [par_id[i] for i in ids]
    pc = get_client()
    stocke = _lire_index(pc, ids)
    for c in a_envoyer:
        avant = stocke.get(c["id"])
        etat = ("déjà à jour" if avant == _texte(c)
                else "absent de l'index" if avant is None
                else "À METTRE À JOUR")
        print(f"  {c['id']:<26} {etat:<18} {_ligne_decision(_texte(c))}")
        if avant is not None and avant != _texte(c):
            print(f"  {'':<26} index actuel       {_ligne_decision(avant)}")

    if args.dry_run:
        print("\n(dry-run : aucune écriture)")
        return

    ensure_index(pc, reset=False)   # jamais de reset ici : on ne touche que ces points
    index_chunks(pc, a_envoyer)

    if args.verifier:
        relu = _lire_index(pc, ids)
        restants = [c["id"] for c in a_envoyer if relu.get(c["id"]) != _texte(c)]
        if restants:
            # L'index est éventuellement consistant : un écart immédiat après
            # l'upsert peut n'être qu'un retard de propagation. On le dit sans
            # dramatiser, plutôt que d'affirmer un succès non constaté.
            print(f"\n⚠️  {len(restants)} point(s) pas encore relu(s) à jour : "
                  f"{', '.join(restants)}\n   (propagation ? relancer --dry-run dans un instant)")
        else:
            print(f"\n✅ Vérifié : les {len(ids)} vecteurs portent bien le nouveau texte.")


if __name__ == "__main__":
    main()
