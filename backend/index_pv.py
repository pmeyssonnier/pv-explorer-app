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
    python index_pv.py --only-year 2021,2022            # n'indexe QUE ces années
    python index_pv.py --commune evere --input pv_conseil_evere.json   # autre commune

RÉINDEXATION CIBLÉE (--only-year) :
    N'envoie que les points des années demandées. Grâce aux ID stables, c'est un
    upsert idempotent : les vecteurs existants sont réécrits sans doublon, les
    autres années restent intactes. Idéal après l'ajout d'une année (ex. 2022) :
    ~10× moins de tokens à ré-embedder → plus de plafond tokens/min saturé.

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
BATCH_SIZE = 60         # burst plus petit = reprise sur 429 moins coûteuse
DEFAULT_JSON = "pv_conseil_schaerbeek.json"

# Plan Pinecone Starter (gratuit) : le modèle d'embedding multilingual-e5-large
# est plafonné à 250 000 tokens/min (input_type "passage"). On cadence l'envoi
# NETTEMENT sous ce plafond (≈ 52 %) : rouler à 80-90 % provoquait des 429 à
# répétition car l'estimation de tokens sous-évalue le français réel. Cette marge
# élimine le thrashing ; le run complet reste ~9 min pour 6,6k points.
SAFE_TOKENS_PER_MIN = 130_000
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
        "year": int(date[:4]) if date[:4].isdigit() else 0,  # filtrage temporel

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


# Libellés lisibles des types de points du chapitrage vidéo.
_VIDEO_TYPE_LABELS = {
    "demande": "Demande", "motion": "Motion", "question": "Question",
    "interpellation": "Interpellation", "prise_acte": "Prise d'acte",
    "approbation": "Approbation", "taxe": "Taxe", "reglement": "Règlement",
    "point": "Point à l'ordre du jour",
}


def video_point_to_chunk(point: dict, seance: dict, commune: str) -> dict:
    """Transforme un point de chapitrage vidéo (YouTube) en chunk indexable.

    Source COMPLÉMENTAIRE aux PV : capte les débats (motions, questions,
    demandes), souvent absents des PV. `source_type="video_conseil"` et l'`url`
    porte le deep-link vers l'instant exact de la vidéo (…&t=SECONDESs), pour un
    lien « ▶ voir le débat » dans les sources. ID stable → upsert idempotent.
    """
    commune_nom = commune.capitalize()
    date = seance.get("date", "date inconnue")
    titre = (point.get("titre_fr") or point.get("titre") or "").strip()
    ptype = point.get("type") or "point"
    auteur = (point.get("auteur") or "").strip()
    start = int(point.get("start_s") or 0)
    deeplink = point.get("deeplink") or seance.get("video_url", "")

    label = _VIDEO_TYPE_LABELS.get(ptype, "Point à l'ordre du jour")
    par = (f" à la demande de {auteur}"
           if auteur and ptype in ("demande", "question", "motion", "interpellation")
           else "")
    chunk_text = (
        f"Conseil communal de {commune_nom} du {date} — point débattu en séance (vidéo).\n"
        f"Titre : {titre}\n"
        f"Type : {label}{par}."
    )

    chunk_id = f"video-{seance.get('video_id', 'x')}-{start}"
    decision = label + (f" · {auteur}" if auteur else "")

    metadata = {
        "chunk_text": chunk_text,          # champ vectorisé
        "commune": commune,
        "date": date,
        "year": int(date[:4]) if date[:4].isdigit() else 0,
        "source_type": "video_conseil",    # distingue des délibérations (PV)
        "sp": 0,                           # non applicable
        "titre": titre[:500],
        "decision": decision,              # ex. « Motion · Elias AMMI »
        "type": ptype,
        "auteur": auteur,
        "url": deeplink,                   # deep-link vers l'instant de la vidéo
        "video_id": seance.get("video_id", "") or "",
        "start_s": start,
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
        if seance.get("video_id"):
            # JSON du chapitrage vidéo (extract_video_chapters.py) : séance à plat
            # avec video_id/points → chunks « video_conseil ».
            commune = default_commune.strip().lower()
            for point in seance.get("points", []):
                chunks.append(video_point_to_chunk(point, seance, commune))
        else:
            # JSON PV classique : {"seance": {...}, "points": [...]}.
            seance_meta = seance.get("seance", {})
            commune = (seance_meta.get("commune") or default_commune).strip().lower()
            for point in seance.get("points", []):
                chunks.append(point_to_chunk(point, seance_meta, commune))
    return chunks


def _estimate_tokens(text: str) -> int:
    """Estimation prudente du nombre de tokens. Le tokenizer e5 découpe le
    français réel (accents, chiffres, noms propres) plus finement que 4 chars/
    token ; on divise par 3.0 pour SUR-estimer franchement → la cadence reste
    sous le plafond même quand le texte est dense en chiffres/montants."""
    return max(1, int(len(text or "") / 3.0))


def _is_rate_limit(e: Exception) -> bool:
    """Détecte un 429 Pinecone quelle que soit la version du SDK."""
    return (
        "RateLimit" in type(e).__name__
        or "429" in str(e)
        or "RESOURCE_EXHAUSTED" in str(e)
    )


MIN_TOKENS_PER_MIN = 40_000     # plancher : en-dessous, la réindexation serait absurde


def _upsert_with_retry(index, records: list[dict]) -> int:
    """Upsert un batch en réessayant sur 429 (plafond tokens/min).
    Retourne le nombre de reprises qu'il a fallu (0 = passé du premier coup) —
    l'appelant s'en sert pour ralentir la cadence globale (auto-adaptation)."""
    for attempt in range(MAX_RATE_RETRIES):
        try:
            index.upsert_records(namespace="pv", records=records)
            return attempt
        except Exception as e:  # noqa: BLE001 — on ne retente que les 429
            if _is_rate_limit(e) and attempt < MAX_RATE_RETRIES - 1:
                print(f"   ⏳ 429 (plafond tokens/min) — attente {RATE_LIMIT_WAIT_SEC}s "
                      f"puis reprise du batch (tentative {attempt + 2}/{MAX_RATE_RETRIES})",
                      flush=True)
                time.sleep(RATE_LIMIT_WAIT_SEC)
                continue
            raise
    return MAX_RATE_RETRIES


def index_chunks(pc: Pinecone, chunks: list[dict]):
    """Upsert les chunks par batches en s'ADAPTANT au vrai débit toléré par
    Pinecone : on part de SAFE_TOKENS_PER_MIN et, à chaque batch qui a dû être
    repris sur 429, on réduit la cadence (× 0,6, plancher MIN_TOKENS_PER_MIN)
    jusqu'à ne plus déclencher de 429. Sorties `flush`ées pour un suivi live
    dans Colab (sinon l'affichage bufferisé donne l'illusion d'un blocage)."""
    index = pc.Index(INDEX_NAME)
    total = len(chunks)
    effective_tpm = SAFE_TOKENS_PER_MIN

    # Baseline : combien de vecteurs déjà présents (diagnostic anti-doublon).
    try:
        base = index.describe_index_stats().get("total_vector_count", "?")
        print(f"ℹ️  Vecteurs déjà dans l'index avant ce run : {base}", flush=True)
    except Exception:
        pass

    print(f"📤 Indexation de {total} chunks (batches de {BATCH_SIZE}, "
          f"cadence initiale ≤ {SAFE_TOKENS_PER_MIN:,} tokens/min, auto-adaptative)...",
          flush=True)

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

        retries = _upsert_with_retry(index, records)
        done = min(i + BATCH_SIZE, total)

        # Auto-adaptation : un 429 = on roule trop vite pour CE plan → on ralentit
        # durablement le reste du run (converge vers le débit réellement toléré).
        if retries > 0 and effective_tpm > MIN_TOKENS_PER_MIN:
            effective_tpm = max(MIN_TOKENS_PER_MIN, int(effective_tpm * 0.6))
            print(f"   ↓ cadence réduite à {effective_tpm:,} tokens/min "
                  f"(429 rencontré)", flush=True)

        print(f"   {done}/{total} chunks indexés", flush=True)

        # Cadence : espace le prochain batch pour rester sous le débit courant.
        if done < total:
            time.sleep(max(0.5, batch_tokens / (effective_tpm / 60.0)))

    print("✅ Indexation terminée", flush=True)

    # Afficher les stats
    time.sleep(3)
    stats = index.describe_index_stats()
    print(f"\n📊 Index '{INDEX_NAME}' :", flush=True)
    print(f"   Vecteurs totaux : {stats.get('total_vector_count', '?')}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Indexe les PV d'une commune dans Pinecone")
    # --input est l'alias privilégié ; --file reste accepté pour compatibilité.
    parser.add_argument("--input", "--file", dest="input", default=DEFAULT_JSON,
                        help="Fichier JSON des PV à indexer")
    parser.add_argument("--commune", default="schaerbeek",
                        help="Nom de la commune (écrit en métadonnée), ex. schaerbeek, evere")
    parser.add_argument("--reset", action="store_true", help="Vide l'index avant réindexation")
    parser.add_argument("--only-year", dest="only_year", default="",
                        help="N'indexe QUE ces années (ex. 2021,2022). Vide = toutes.")
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

    if args.only_year:
        years = {int(y) for y in args.only_year.split(",") if y.strip().isdigit()}
        if not years:
            print(f"❌ --only-year invalide : « {args.only_year} » (ex. attendu : 2021,2022)")
            sys.exit(1)
        before = len(chunks)
        chunks = [c for c in chunks if c["metadata"].get("year") in years]
        print(f"🎯 Filtre --only-year {sorted(years)} : {before} → {len(chunks)} chunks à (ré)indexer")

    if not chunks:
        print("❌ Aucun point à indexer")
        sys.exit(1)

    index_chunks(pc, chunks)
    print("\n🎉 Terminé ! L'index est prêt pour les requêtes.")


if __name__ == "__main__":
    main()
