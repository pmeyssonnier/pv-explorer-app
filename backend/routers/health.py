"""Routes de service : racine et /health (état des clés + accès à l'index)."""
import os

from fastapi import APIRouter

from config import logger
from services.pinecone_service import get_pinecone_index

router = APIRouter()


@router.get("/")
def root():
    return {"service": "PV Schaerbeek Q&R", "status": "ok", "version": "0.1"}


@router.get("/health")
def health():
    """Vérifie que les clés sont configurées et l'index accessible."""
    checks = {
        "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "pinecone_key": bool(os.environ.get("PINECONE_API_KEY")),
    }
    try:
        idx = get_pinecone_index()
        stats = idx.describe_index_stats()
        checks["index_vectors"] = stats.get("total_vector_count", 0)
        checks["index_ok"] = True
    except Exception:
        # Ne pas exposer le détail de l'exception au client (fuite d'infos).
        logger.exception("Échec de la vérification de l'index Pinecone")
        checks["index_ok"] = False
    return checks
