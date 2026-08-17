"""Schémas Pydantic de l'API (requêtes/réponses). Contrat HTTP du backend."""
from typing import Optional

from pydantic import BaseModel, Field

from config import MAX_QUESTION_LEN


class QuestionRequest(BaseModel):
    # Longueur bornée : protège le budget API (une question démesurée serait
    # envoyée telle quelle à Claude). Pydantic renvoie 422 hors bornes.
    question: str = Field(min_length=3, max_length=MAX_QUESTION_LEN)
    # Commune optionnelle. Absente ou "toutes" → recherche croisée (aucun
    # filtre, comportement historique). Sinon → filtre sur cette commune.
    commune: Optional[str] = Field(default=None, max_length=50)


class Source(BaseModel):
    date: str
    sp: int
    titre: str
    decision: str
    score: float


class AnswerResponse(BaseModel):
    answer: str
    sources: list[Source]
