"""Intègre une nouvelle question écrite uploadée par l'admin : extraction
(réutilise pipeline/questions_ecrites_extraction_pipeline.py, le même module
qui traite les lots depuis Google Drive/Colab — voir son docstring), fusion
en mémoire, publication (commit GitHub + réindexation Pinecone de cette seule
question). Même flux en 2 temps que services/pv_integration.py — voir sa
docstring pour le raisonnement complet (état du backend, persistance via
GitHub uniquement, pas de save disque).

Réindexation : un seul chunk par question (document court, voir
index_qe.py) — même index/namespace que les PV, 3e source_type
"question_ecrite" filtré à part côté lecture (services/rag.py). Le backfill
du corpus déjà publié (traité par lot Colab, jamais passé par cette
fonction) se fait à part via `python index_qe.py` — voir sa docstring.
"""
import json
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

# pipeline/ est un répertoire FRÈRE de backend/ — même mécanique que
# services/pv_integration.py (voir sa docstring pour le détail rootDir=backend).
_PIPELINE_DIR = str(Path(__file__).resolve().parent.parent.parent / "pipeline")
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import questions_ecrites_extraction_pipeline as qe_pipeline  # noqa: E402

import index_qe  # noqa: E402
from services.github_publish import commit_file  # noqa: E402
from services.pinecone_service import get_pinecone_client  # noqa: E402
from services.questions_ecrites import QE_JSON_PATH, load_qe_db  # noqa: E402

COMMUNE = "schaerbeek"


def extract_from_upload(
    pdf_bytes: bytes, filename: str, progress_cb: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Écrit le PDF dans un fichier temporaire, lance l'extraction complète
    (qe_pipeline.process_pdf), retourne la question structurée. Lève
    ValueError si l'extraction échoue (PDF scanné/image, ou numéro/date/
    auteur·e non identifiables — voir normalize_question)."""
    safe_name = Path(filename).name or "upload.pdf"
    with tempfile.TemporaryDirectory(prefix="qe_upload_") as tmp_dir:
        pdf_path = Path(tmp_dir) / safe_name
        pdf_path.write_bytes(pdf_bytes)
        question = qe_pipeline.process_pdf(pdf_path, progress_cb=progress_cb)
    if question is None:
        raise ValueError(
            "Extraction impossible : PDF scanné/image, ou numéro/date/auteur·e non identifiables."
        )
    return question


def extract_and_preview(pdf_bytes: bytes, filename: str, progress_cb=None) -> dict:
    """Combine extraction + aperçu de fusion — tourne en tâche de fond (voir
    routers/admin.py + services/jobs.py), pas extract_from_upload seule."""
    question = extract_from_upload(pdf_bytes, filename, progress_cb=progress_cb)
    return {"question": question, "preview": preview_merge(question)}


def preview_merge(question: dict) -> dict:
    """Dit à l'admin si c'est une nouvelle entrée ou une correction d'une
    entrée déjà publiée (même année+numéro), SANS rien modifier."""
    db = load_qe_db()
    existing = next((q for q in db["questions"] if q.get("id") == question["id"]), None)
    return {
        "id": question["id"],
        "annee": question["annee"],
        "numero": question["numero"],
        "is_new": existing is None,
    }


def publish_question(question: dict, source_url: Optional[str] = None, progress_cb=None) -> dict:
    """Fusionne pour de vrai, committe sur GitHub, réindexe cette question dans
    Pinecone. Retourne un résumé {commit_sha, id, auteur}. `progress_cb`
    (optionnel, voir jobs.start_job) : 2 étapes grossières ("commit" /
    "indexing"), même convention que services/pv_integration.publish_seance."""
    if not question.get("date") or not question.get("numero") or not question.get("auteur"):
        raise ValueError("Question incomplète (date/numéro/auteur·e manquant) — rien à publier.")
    if source_url:
        question["source_url"] = source_url

    if progress_cb:
        progress_cb({"stage": "commit"})

    db = load_qe_db()
    qe_pipeline.merge_question_into_db(db, question)
    db["meta"]["total_questions"] = len(db["questions"])

    content = json.dumps(db, ensure_ascii=False, indent=2)
    commit_sha = commit_file(
        f"backend/{QE_JSON_PATH}",
        content,
        f"data: intègre la question écrite n°{question['numero']}/{question['annee']} "
        f"de {question['auteur']} (via panneau admin)",
    )

    if progress_cb:
        progress_cb({"stage": "indexing"})
    _index_question(question)

    return {"commit_sha": commit_sha, "id": question["id"], "auteur": question["auteur"]}


def _index_question(question: dict) -> None:
    """Réindexe SEULEMENT cette question (pas tout le corpus) — upsert
    idempotent par ID stable (voir index_qe.question_to_chunk)."""
    chunk = index_qe.question_to_chunk(question, COMMUNE)
    pc = get_pinecone_client()
    index_qe.index_chunks(pc, [chunk])
