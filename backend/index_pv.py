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
    python index_pv.py                                  # Schaerbeek (défaut)
    python index_pv.py --reset                          # vide l'index puis réindexe
    python index_pv.py --commune evere --input pv_conseil_evere.json   # autre commune

MULTI-COMMUNE :
    Un seul index, un seul namespace ("pv"). Chaque vecteur porte une
    métadonnée `commune` ("schaerbeek", "evere", …) pour permettre un filtrage
    optionnel côté recherche. Sans --reset, chaque commune s'ajoute/se met à
    jour par upsert idempotent (ID stable) sans toucher aux autres.

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

# Plan Pinecone Starter (gratuit) : le modèle d'embedding multilingual-e5-large
# est plafonné à 250 000 tokens/min (input_type "passage"). On cadence l'envoi
# sous une marge de sécurité et on réessaie sur 429.
SAFE_TOKENS_PER_MIN = 200_000
RATE_LIMIT_WAIT_SEC = 65        # attendre > 60 s réinitialise la fenêtre par minute
MAX_RATE_RETRIES = 8


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


def point_to_chunk(point: dict, seance: dict, commune: str) -> dict:
    """
    Transforme un point de PV en chunk indexable.
    Le texte contient tout le contexte pour que la recherche soit pertinente.
    `commune` (ex. "schaerbeek") est écrit en métadonnée pour le filtrage
    multi-commune et injecté dans le texte vectorisé.
    """
    commune_nom = commune.capitalize()
    date = seance.get("date", "date inconnue")
    montant = point.get("montant_eur")
    montant_str = f"\nMontant : {montant:,.0f} €".replace(",", " ") if montant else ""
    intervenants = point.get("intervenants") or []
    interv_str = f"\nIntervenants : {', '.join(intervenants)}" if intervenants else ""
    repondant = point.get("repondant")
    rep_str = f"\nRépondant : {repondant}" if repondant else ""

    # Le texte qui sera vectorisé et recherché
    chunk_text = f"""Séance du Conseil communal de {commune_nom} du {date}.
Point SP {point.get('sp','?')} — {point.get('rubrique','')} / {point.get('sous_rubrique','')}
Titre : {point.get('titre','')}
Résumé : {point.get('resume','')}
Décision : {point.get('decision','')}
Vote : {format_vote(point.get('vote'))}{montant_str}{interv_str}{rep_str}
Thématiques : {', '.join(point.get('thematiques') or [])}"""

    # ID unique et stable pour ce point. Inchangé (pas de préfixe commune) pour
    # que la réindexation d'une commune existante reste un upsert idempotent.
    # ⚠️ Les séances de communes différentes doivent avoir des `id` distincts
    #    (la pipeline d'extraction d'Evere doit garantir cette unicité).
    chunk_id = f"{seance.get('id','PV')}_SP{point.get('sp','?')}"

    # Métadonnées pour filtrage et affichage (Pinecone limite à ~40KB par vecteur)
    metadata = {
        "chunk_text": chunk_text,          # champ vectorisé
        "commune": commune,                # filtrage multi-commune
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


def load_chunks(json_path: Path, default_commune: str) -> list[dict]:
    """Charge le JSON des PV et le transforme en liste de chunks.

    La commune de chaque vecteur est lue dans seance["commune"] si présente
    (cas d'un JSON multi-commune produit par la pipeline d'extraction), sinon
    on retombe sur `default_commune` (fichier mono-commune + argument --commune).
    Rétro-compatible : le JSON Schaerbeek historique n'a pas de champ commune
    → tout est indexé avec default_commune (schaerbeek).
    """
    with open(json_path, encoding="utf-8") as f:
        db = json.load(f)

    chunks = []
    for seance in db.get("seances", []):
        seance_meta = seance.get("seance", {})
        commune = (seance_meta.get("commune") or default_commune).strip().lower()
        for point in seance.get("points", []):
            chunks.append(point_to_chunk(point, seance_meta, commune))
    return chunks


def _estimate_tokens(text: str) -> int:
    """Estimation prudente du nombre de tokens (français ≈ 4 chars/token ;
    on divise par 3.5 pour SUR-estimer → marge de sécurité côté cadence)."""
    return max(1, int(len(text or "") / 3.5))


def _is_rate_limit(e: Exception) -> bool:
    """Détecte un 429 Pinecone quelle que soit la version du SDK."""
    return (
        "RateLimit" in type(e).__name__
        or "429" in str(e)
        or "RESOURCE_EXHAUSTED" in str(e)
    )


def _upsert_with_retry(index, records: list[dict]):
    """Upsert un batch en réessayant sur 429 (plafond tokens/min)."""
    for attempt in range(MAX_RATE_RETRIES):
        try:
            index.upsert_records(namespace="pv", records=records)
            return
        except Exception as e:  # noqa: BLE001 — on ne retente que les 429
            if _is_rate_limit(e) and attempt < MAX_RATE_RETRIES - 1:
                print(f"   ⏳ 429 (plafond tokens/min) — attente {RATE_LIMIT_WAIT_SEC}s "
                      f"puis reprise du batch (tentative {attempt + 2}/{MAX_RATE_RETRIES})")
                time.sleep(RATE_LIMIT_WAIT_SEC)
                continue
            raise


def index_chunks(pc: Pinecone, chunks: list[dict]):
    """Upsert les chunks dans Pinecone par batches, en respectant le plafond
    de tokens/min du plan gratuit (cadence proportionnelle + retry sur 429)."""
    index = pc.Index(INDEX_NAME)
    total = len(chunks)
    tokens_per_sec = SAFE_TOKENS_PER_MIN / 60.0
    print(f"📤 Indexation de {total} chunks (batches de {BATCH_SIZE}, "
          f"cadence ≤ {SAFE_TOKENS_PER_MIN:,} tokens/min)...")

    for i in range(0, total, BATCH_SIZE):
        batch = chunks[i:i + BATCH_SIZE]
        # Format pour upsert avec embedding intégré : liste de records
        records = []
        batch_tokens = 0
        for c in batch:
            record = {"id": c["id"]}
            record.update(c["metadata"])   # inclut chunk_text qui sera vectorisé
            records.append(record)
            batch_tokens += _estimate_tokens(c["metadata"].get("chunk_text", ""))

        _upsert_with_retry(index, records)
        done = min(i + BATCH_SIZE, total)
        print(f"   {done}/{total} chunks indexés")

        # Cadence : espace le prochain batch pour rester sous le plafond/min.
        if done < total:
            time.sleep(max(0.5, batch_tokens / tokens_per_sec))

    print("✅ Indexation terminée")

    # Afficher les stats
    time.sleep(3)
    stats = index.describe_index_stats()
    print(f"\n📊 Index '{INDEX_NAME}' :")
    print(f"   Vecteurs totaux : {stats.get('total_vector_count', '?')}")


def main():
    parser = argparse.ArgumentParser(description="Indexe les PV d'une commune dans Pinecone")
    # --input est l'alias privilégié ; --file reste accepté pour compatibilité.
    parser.add_argument("--input", "--file", dest="input", default=DEFAULT_JSON,
                        help="Fichier JSON des PV à indexer")
    parser.add_argument("--commune", default="schaerbeek",
                        help="Nom de la commune (écrit en métadonnée), ex. schaerbeek, evere")
    parser.add_argument("--reset", action="store_true", help="Vide l'index avant réindexation")
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
    print(f"  INDEXATION PV {commune.upper()} → PINECONE")
    print("═" * 60)

    pc = get_client()
    # ⚠️ --reset vide TOUT l'index (toutes communes). À n'utiliser que pour une
    #    reconstruction complète. Pour ajouter/mettre à jour une commune sans
    #    toucher aux autres, lancer SANS --reset (upsert idempotent par ID).
    ensure_index(pc, reset=args.reset)

    chunks = load_chunks(json_path, commune)
    print(f"📄 {len(chunks)} points chargés depuis {json_path.name} "
          f"(commune par défaut : {commune} ; seance['commune'] prioritaire si présent)")

    if not chunks:
        print("❌ Aucun point à indexer")
        sys.exit(1)

    index_chunks(pc, chunks)
    print("\n🎉 Terminé ! L'index est prêt pour les requêtes.")


if __name__ == "__main__":
    main()
