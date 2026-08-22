"""Accès à l'index Pinecone (client paresseux). La clé API reste côté serveur ;
le client n'est instancié qu'au premier appel réel (démarrage rapide, tests sans clé).
"""
import os
from typing import Optional

from pinecone import Pinecone

from config import INDEX_NAME

_pc: Optional[Pinecone] = None


def get_pinecone_client() -> Pinecone:
    global _pc
    if _pc is None:
        key = os.environ.get("PINECONE_API_KEY", "")
        if not key:
            raise RuntimeError("PINECONE_API_KEY manquante côté serveur")
        _pc = Pinecone(api_key=key)
    return _pc


def get_pinecone_index():
    return get_pinecone_client().Index(INDEX_NAME)
