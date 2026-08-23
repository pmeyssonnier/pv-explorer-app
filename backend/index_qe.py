"""
╔══════════════════════════════════════════════════════════════════════════╗
║  INDEXATION DES QUESTIONS ÉCRITES → PINECONE                              ║
╚══════════════════════════════════════════════════════════════════════════╝

Réutilise le MÊME index/namespace que les PV et le chapitrage vidéo (voir
index_pv.py) — un 3e `source_type` ("question_ecrite") parmi les vecteurs,
filtré et rendu à part côté lecture (services/rag.py), sur le même principe
que "video_conseil". Document court, UN SEUL chunk par question (pas de
découpage en sous-segments, contrairement à un point de PV dense ou un
transcript vidéo).

USAGE (backfill ponctuel du corpus déjà publié — upsert idempotent par ID
stable, sans risque de doublon en le relançant) :
    export PINECONE_API_KEY="pcsk_..."
    cd backend && python3 index_qe.py
    python3 index_qe.py --input questions_ecrites_schaerbeek.json --commune schaerbeek

La publication ponctuelle (panneau admin) réindexe automatiquement la
question concernée — voir services/questions_ecrites_integration.py. Ce
script sert seulement au backfill initial (ou à une réindexation complète
après un changement de forme des métadonnées).
"""
import argparse
import json
import sys
from pathlib import Path

from index_pv import SCHEMA_VERSION, get_client, index_chunks  # réutilise get_client/l'upsert batché


def question_to_chunk(q: dict, commune: str) -> dict:
    """Transforme une question écrite en chunk indexable. `decision` (réutilisé
    ici comme pour un débat vidéo — voir index_pv.video_point_to_chunks — un
    contexte compact plutôt qu'une issue de vote) porte « Question de X » et,
    si connu, « · répondu par Y »."""
    commune_nom = commune.capitalize()
    auteur = (q.get("auteur") or "").strip()
    repondant = (q.get("repondant") or "").strip()
    reponse = q.get("reponse")
    date = q.get("date") or "date inconnue"

    decision = f"Question de {auteur}" if auteur else ""
    if repondant:
        decision += f" · répondu par {repondant}"

    chunk_text = f"""{q.get('titre', '')}

Question écrite adressée au Collège des Bourgmestre et Échevins de {commune_nom}, \
le {date}, par {auteur}.
Question : {q.get('question', '')}
Réponse : {reponse or "Pas encore de réponse publiée."}"""

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "chunk_text": chunk_text,
        "commune": commune,
        "date": date,
        "year": int(q.get("annee") or 0),
        "source_type": "question_ecrite",
        "sp": 0,                            # non applicable
        "titre": (q.get("titre") or "")[:500],
        "decision": decision,
        "type": "question_ecrite",
        "url": q.get("source_url") or "",
        "thematiques": [],                  # non extraites pour les questions écrites
        # Dupliquée hors chunk_text (qui n'est jamais renvoyé au frontend, voir
        # rag.build_context) : nécessaire pour l'accordéon « Voir la réponse »
        # dans les sources du chat, comme pour l'onglet Par élu·e.
        "reponse": reponse or "",
    }
    return {"id": q["id"], "metadata": metadata}


def load_chunks(json_path: Path, commune: str) -> list[dict]:
    with open(json_path, encoding="utf-8") as f:
        db = json.load(f)
    return [question_to_chunk(q, commune) for q in db.get("questions", []) if q.get("id")]


def main():
    parser = argparse.ArgumentParser(description="Indexe les questions écrites dans Pinecone")
    parser.add_argument("--input", default="questions_ecrites_schaerbeek.json",
                        help="Fichier JSON des questions écrites à indexer")
    parser.add_argument("--commune", default="schaerbeek",
                        help="Nom de la commune (écrit en métadonnée), ex. schaerbeek")
    args = parser.parse_args()

    commune = args.commune.strip().lower()
    if not commune:
        print("❌ --commune ne peut pas être vide")
        sys.exit(1)

    json_path = Path(args.input)
    if not json_path.exists():
        print(f"❌ Fichier introuvable : {json_path}")
        sys.exit(1)

    print("═" * 60)
    print(f"  INDEXATION QUESTIONS ÉCRITES {commune.upper()} → PINECONE")
    print("═" * 60)

    pc = get_client()
    chunks = load_chunks(json_path, commune)
    print(f"📄 {len(chunks)} question(s) chargée(s) depuis {json_path.name}")

    if not chunks:
        print("❌ Aucune question à indexer")
        sys.exit(1)

    index_chunks(pc, chunks)
    print("\n🎉 Terminé ! L'index est prêt pour les requêtes.")


if __name__ == "__main__":
    main()
