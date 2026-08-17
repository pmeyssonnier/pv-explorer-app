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
    """État PUBLIC minimal : le service tourne et l'index est joignable.

    Volontairement sobre : un endpoint public n'a pas à révéler quels
    fournisseurs ont une clé configurée, ni le nombre exact de vecteurs. Ces
    détails ne vont QUE dans les logs (niveau debug, pour ne pas noyer les pings
    fréquents de Render), et la traceback complète est loguée en cas d'échec.
    """
    logger.debug(
        "health — anthropic_key=%s pinecone_key=%s",
        bool(os.environ.get("ANTHROPIC_API_KEY")),
        bool(os.environ.get("PINECONE_API_KEY")),
    )
    index_status = "error"
    try:
        stats = get_pinecone_index().describe_index_stats()
        logger.debug("health — index_vectors=%s", stats.get("total_vector_count", 0))
        index_status = "ok"
    except Exception:
        # Détail côté serveur uniquement (jamais renvoyé au client).
        logger.exception("Échec de la vérification de l'index Pinecone")
    return {"status": "ok", "index": index_status}
