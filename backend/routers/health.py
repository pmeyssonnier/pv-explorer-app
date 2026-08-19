"""Routes de service : racine et /health (état des clés + accès à l'index)."""
import os

from fastapi import APIRouter

from config import VERSION, logger
from services.pinecone_service import get_pinecone_index

router = APIRouter()


@router.get("/")
def root():
    return {"service": "PV Schaerbeek Q&R", "status": "ok", "version": VERSION}


@router.get("/health")
def health():
    """LIVENESS : le process répond. AUCUN appel externe — c'est cet endpoint
    que Render ping fréquemment (healthCheckPath), il doit rester instantané et
    ne pas coupler la santé du service à la disponibilité de Pinecone.

    Expose aussi `version` (constante, aucun coût) : c'est la SOURCE UNIQUE de
    version, lue par le frontend pour l'afficher (plus de numéro dupliqué)."""
    return {"status": "ok", "version": VERSION}


@router.get("/ready")
def ready():
    """READINESS : les dépendances externes (index Pinecone) sont joignables.

    À utiliser pour la supervision, pas pour le health check fréquent. Réponse
    publique sobre : ni présence des clés, ni volume de vecteurs (détails en
    logs debug ; traceback complète loguée en cas d'échec)."""
    logger.debug(
        "ready — anthropic_key=%s pinecone_key=%s",
        bool(os.environ.get("ANTHROPIC_API_KEY")),
        bool(os.environ.get("PINECONE_API_KEY")),
    )
    index_status = "error"
    try:
        stats = get_pinecone_index().describe_index_stats()
        logger.debug("ready — index_vectors=%s", stats.get("total_vector_count", 0))
        index_status = "ok"
    except Exception:
        # Détail côté serveur uniquement (jamais renvoyé au client).
        logger.exception("Échec de la vérification de l'index Pinecone")
    return {"status": "ok" if index_status == "ok" else "degraded", "index": index_status}
