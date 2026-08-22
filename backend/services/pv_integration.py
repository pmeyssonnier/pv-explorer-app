"""Intègre un nouveau PV uploadé par l'admin : extraction (réutilise le
pipeline `pipeline/pv_extraction_pipeline.py`, le même qui a produit
pv_conseil_schaerbeek.json — voir son historique git), fusion en mémoire,
publication (commit GitHub + réindexation Pinecone des points concernés).

Flux en 2 temps, PAS un upload → publication directe :
  1. extract_from_upload() : extraction + audit de complétude déterministe
     (comptage regex des ancres "SP n.-" vs points extraits — voir
     verify_completeness dans le pipeline). Rien n'est persisté. Le résultat
     complet repart vers le navigateur (pas de stockage serveur entre les 2
     étapes — le backend est sans état, un seul process Render).
  2. publish_seance() : reçoit EXACTEMENT ce que l'étape 1 a renvoyé (l'admin
     a eu l'occasion de vérifier l'aperçu avant de confirmer), fusionne pour
     de vrai, committe sur GitHub (seul moyen de PERSISTER — le disque du
     backend est éphémère) et réindexe.

Le disque de Render n'est PAS écrit ici (pas d'appel à save_database) : un
commit + une lecture ultérieure depuis PV_JSON_PATH après redéploiement est
la seule source de vérité durable.
"""
import json
import sys
import tempfile
from pathlib import Path
from typing import Optional

# pipeline/ est un répertoire FRÈRE de backend/ (pas un sous-package importable
# tel quel) — même mécanique que tests/conftest.py pour le rendre importable,
# nécessaire ici car render.yaml a rootDir=backend (pipeline/ n'est pas sur
# sys.path par défaut au démarrage de l'app ; backend/ lui-même l'est déjà —
# c'est pour ça que `import index_pv` plus bas n'a besoin d'aucun sys.path.insert).
_PIPELINE_DIR = str(Path(__file__).resolve().parent.parent.parent / "pipeline")
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import pv_extraction_pipeline as pipeline  # noqa: E402
import index_pv  # noqa: E402

from config import PV_JSON_PATH  # noqa: E402
from services.github_publish import commit_file  # noqa: E402
from services import pinecone_service  # noqa: E402

COMMUNE = "schaerbeek"

# Cache de la pipeline redirigé vers un répertoire réellement inscriptible sur
# Render (le défaut du pipeline pointe vers un chemin Google Drive/Colab qui
# n'existe pas ici — mkdir(parents=True) le créerait quand même, mais autant
# éviter une arborescence /content/... parasite sur le disque du backend).
pipeline.CONFIG["CACHE_DIR"] = str(Path(tempfile.gettempdir()) / "pv_pipeline_cache")


def extract_from_upload(pdf_bytes: bytes, filename: str) -> dict:
    """Écrit le PDF dans un fichier temporaire, lance l'extraction complète
    (pipeline.process_pdf), retourne la structure de séance brute — telle
    quelle, y compris seance["extraction_check"] (le rapport de complétude).
    Lève ValueError si l'extraction échoue (PDF scanné/image, aucun texte)."""
    safe_name = Path(filename).name or "upload.pdf"
    with tempfile.TemporaryDirectory(prefix="pv_upload_") as tmp_dir:
        pdf_path = Path(tmp_dir) / safe_name
        pdf_path.write_bytes(pdf_bytes)
        seance_struct = pipeline.process_pdf(pdf_path)
    if seance_struct is None:
        raise ValueError(
            "Aucun texte exploitable extrait du PDF (scanné/image ?) ou aucun point trouvé."
        )
    return seance_struct


def extract_and_preview(pdf_bytes: bytes, filename: str) -> dict:
    """Combine extraction + aperçu de fusion — c'est cette fonction qui tourne
    en tâche de fond (voir routers/admin.py + services/jobs.py), pas
    extract_from_upload seule, pour que la route /extract renvoie un job_id
    immédiatement plutôt que de bloquer sur toute la durée de l'extraction."""
    seance = extract_from_upload(pdf_bytes, filename)
    return {"seance": seance, "preview": preview_merge(seance)}


def _load_current_db() -> dict:
    with open(PV_JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def preview_merge(seance_struct: dict) -> dict:
    """Simule la fusion (sur une copie de la base réelle) pour dire à l'admin
    si c'est une nouvelle séance ou une mise à jour, SANS rien modifier."""
    db = _load_current_db()
    date = (seance_struct.get("seance") or {}).get("date")
    existing = next((s for s in db["seances"] if s["seance"]["date"] == date), None)
    return {
        "date": date,
        "n_points": len(seance_struct.get("points") or []),
        "is_new": existing is None,
        "existing_points": len(existing["points"]) if existing else 0,
    }


def publish_seance(seance_struct: dict, source_url: Optional[str] = None) -> dict:
    """Fusionne pour de vrai, committe sur GitHub, réindexe dans Pinecone.
    Retourne un résumé {commit_sha, date, n_points, indexed}."""
    meta = seance_struct.get("seance") or {}
    date = meta.get("date")
    if not date or not seance_struct.get("points"):
        raise ValueError("Séance sans date ou sans points — rien à publier.")
    if source_url:
        meta["source_url"] = source_url

    db = _load_current_db()
    pipeline.merge_seance_into_db(db, seance_struct)
    db["meta"]["seances_incluses"] = sorted(
        {s["seance"]["date"] for s in db["seances"] if s["seance"]["date"]}
    )
    db["meta"]["total_points"] = sum(len(s["points"]) for s in db["seances"])

    content = json.dumps(db, ensure_ascii=False, indent=2)
    commit_sha = commit_file(
        f"backend/{PV_JSON_PATH}",
        content,
        f"data: intègre le PV du {date} (via panneau admin)",
    )

    n_indexed = _index_seance_points(seance_struct)
    return {
        "commit_sha": commit_sha,
        "date": date,
        "n_points": len(seance_struct["points"]),
        "indexed": n_indexed,
    }


def _index_seance_points(seance_struct: dict) -> int:
    """Réindexe SEULEMENT les points de cette séance (pas tout le corpus) —
    upsert idempotent par ID stable (voir index_pv.point_to_chunk), ne touche
    pas aux autres vecteurs."""
    meta = seance_struct["seance"]
    chunks = [index_pv.point_to_chunk(p, meta, COMMUNE) for p in seance_struct["points"]]
    pc = pinecone_service.get_pinecone_client()
    index_pv.index_chunks(pc, chunks)
    return len(chunks)
