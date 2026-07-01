"""
╔══════════════════════════════════════════════════════════════════════════╗
║  INDEXATION DES PV → PINECONE                          PROTOTYPE v0.1     ║
║  Découpe la base JSON des PV en chunks et les indexe dans Pinecone       ║
║  Embeddings intégrés Pinecone (multilingual-e5-large) — 1 seule clé API  ║
╚══════════════════════════════════════════════════════════════════════════╝

PRÉREQUIS :
    pip install pinecone
    export PINECONE_API_KEY="pcsk_..."

USAGE :
    python index_pv.py                    # indexe pv_conseil_schaerbeek.json
    python index_pv.py --reset            # vide l'index avant de réindexer
    python index_pv.py --file autre.json  # indexe un autre fichier

Le script crée un index Pinecone "pv-explorer" avec le modèle d'embedding
intégré multilingual-e5-large (1024 dimensions, gère bien le français).
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path

from pinecone import Pinecone

# ── CONFIG ──────────────────────────────────────────────────────────────────
INDEX_NAME = "pv-explorer"
EMBED_MODEL = "multilingual-e5-large"   # embeddings intégrés Pinecone, multilingue
CLOUD = "aws"
REGION = "us-east-1"
DIMENSION = 1024
BATCH_SIZE = 90         # Pinecone limite les upserts d'embeddings intégrés par batch
DEFAULT_JSON = "pv_conseil_schaerbeek.json"


def get_client() -> Pinecone:
    api_key = os.environ.get("PINECONE_API_KEY", "")
    if not api_key:
        print("❌ Variable d'environnement PINECONE_API_KEY manquante.")
        print("   Obtiens une clé gratuite sur https://app.pinecone.io")
        sys.exit(1)
    return Pinecone(api_key=api_key)


def ensure_index(pc: Pinecone, reset: bool = False):
    """Crée l'index avec embedding intégré s'il n'existe pas."""
    existing = [ix["name"] for ix in pc.list_indexes()]

    if reset and INDEX_NAME in existing:
        print(f"🗑  Suppression de l'index existant '{INDEX_NAME}'...")
        pc.delete_index(INDEX_NAME)
        time.sleep(5)
        existing = [ix["name"] for ix in pc.list_indexes()]

    if INDEX_NAME not in existing:
        print(f"🔨 Création de l'index '{INDEX_NAME}' (modèle {EMBED_MODEL})...")
        # Index avec embedding intégré : Pinecone génère les vecteurs lui-même
        pc.create_index_for_model(
            name=INDEX_NAME,
            cloud=CLOUD,
            region=REGION,
            embed={
                "model": EMBED_MODEL,
                "field_map": {"text": "chunk_text"}   # champ à vectoriser
            }
        )
        # Attendre que l'index soit prêt
        while not pc.describe_index(INDEX_NAME).status["ready"]:
            print("   ...index en cours de création...")
            time.sleep(3)
        print("✅ Index prêt")
    else:
        print(f"✅ Index '{INDEX_NAME}' existe déjà")


def format_vote(vote: dict) -> str:
    """Transforme un vote en texte lisible."""
    if not vote:
        return "vote non précisé"
    t = vote.get("type")
    if t == "unanimite":
        return "approuvé à l'unanimité"
    if t == "vote_nominal":
        return f"{vote.get('pour','?')} pour, {vote.get('contre',0)} contre, {vote.get('abstentions',0)} abstentions"
    if t == "reporte":
        return "point reporté"
    return "vote non précisé"


def point_to_chunk(point: dict, seance: dict) -> dict:
    """
    Transforme un point de PV en chunk indexable.
    Le texte contient tout le contexte pour que la recherche soit pertinente.
    """
    date = seance.get("date", "date inconnue")
    montant = point.get("montant_eur")
    montant_str = f"\nMontant : {montant:,.0f} €".replace(",", " ") if montant else ""
    intervenants = point.get("intervenants") or []
    interv_str = f"\nIntervenants : {', '.join(intervenants)}" if intervenants else ""
    repondant = point.get("repondant")
    rep_str = f"\nRépondant : {repondant}" if repondant else ""

    # Le texte qui sera vectorisé et recherché
    chunk_text = f"""Séance du Conseil communal de Schaerbeek du {date}.
Point SP {point.get('sp','?')} — {point.get('rubrique','')} / {point.get('sous_rubrique','')}
Titre : {point.get('titre','')}
Résumé : {point.get('resume','')}
Décision : {point.get('decision','')}
Vote : {format_vote(point.get('vote'))}{montant_str}{interv_str}{rep_str}
Thématiques : {', '.join(point.get('thematiques') or [])}"""

    # ID unique et stable pour ce point
    chunk_id = f"{seance.get('id','PV')}_SP{point.get('sp','?')}"

    # Métadonnées pour filtrage et affichage (Pinecone limite à ~40KB par vecteur)
    metadata = {
        "chunk_text": chunk_text,          # champ vectorisé
        "date": date,
        "sp": int(point.get("sp", 0)) if str(point.get("sp", "")).isdigit() else 0,
        "rubrique": point.get("rubrique", "") or "",
        "titre": (point.get("titre", "") or "")[:500],
        "decision": point.get("decision", "") or "",
        "type": point.get("type", "") or "",
        "montant_eur": float(montant) if montant else 0.0,
        "thematiques": point.get("thematiques") or [],
        "vote_type": (point.get("vote") or {}).get("type", "") or "",
    }
    return {"id": chunk_id, "metadata": metadata}


def load_chunks(json_path: Path) -> list[dict]:
    """Charge le JSON des PV et le transforme en liste de chunks."""
    with open(json_path, encoding="utf-8") as f:
        db = json.load(f)

    chunks = []
    for seance in db.get("seances", []):
        seance_meta = seance.get("seance", {})
        for point in seance.get("points", []):
            chunks.append(point_to_chunk(point, seance_meta))
    return chunks


def index_chunks(pc: Pinecone, chunks: list[dict]):
    """Upsert les chunks dans Pinecone par batches."""
    index = pc.Index(INDEX_NAME)
    total = len(chunks)
    print(f"📤 Indexation de {total} chunks (batches de {BATCH_SIZE})...")

    for i in range(0, total, BATCH_SIZE):
        batch = chunks[i:i + BATCH_SIZE]
        # Format pour upsert avec embedding intégré : liste de records
        records = []
        for c in batch:
            record = {"id": c["id"]}
            record.update(c["metadata"])   # inclut chunk_text qui sera vectorisé
            records.append(record)
        index.upsert_records(namespace="pv", records=records)
        done = min(i + BATCH_SIZE, total)
        print(f"   {done}/{total} chunks indexés")
        time.sleep(0.5)

    print("✅ Indexation terminée")

    # Afficher les stats
    time.sleep(3)
    stats = index.describe_index_stats()
    print(f"\n📊 Index '{INDEX_NAME}' :")
    print(f"   Vecteurs totaux : {stats.get('total_vector_count', '?')}")


def main():
    parser = argparse.ArgumentParser(description="Indexe les PV dans Pinecone")
    parser.add_argument("--file", default=DEFAULT_JSON, help="Fichier JSON des PV")
    parser.add_argument("--reset", action="store_true", help="Vide l'index avant réindexation")
    args = parser.parse_args()

    json_path = Path(args.file)
    if not json_path.exists():
        print(f"❌ Fichier introuvable : {json_path}")
        sys.exit(1)

    print("═" * 60)
    print("  INDEXATION PV SCHAERBEEK → PINECONE")
    print("═" * 60)

    pc = get_client()
    ensure_index(pc, reset=args.reset)

    chunks = load_chunks(json_path)
    print(f"📄 {len(chunks)} points chargés depuis {json_path.name}")

    if not chunks:
        print("❌ Aucun point à indexer")
        sys.exit(1)

    index_chunks(pc, chunks)
    print("\n🎉 Terminé ! L'index est prêt pour les requêtes.")


if __name__ == "__main__":
    main()
