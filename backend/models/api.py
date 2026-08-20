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
    # Overrides optionnels du menu « Options » (front). Re-bornés côté serveur
    # dans rag.answer() ; ces bornes larges ne sont qu'une 1re barrière (422).
    top_k: Optional[int] = Field(default=None, ge=1, le=100)
    max_sources: Optional[int] = Field(default=None, ge=1, le=50)
    score_min: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    model: Optional[str] = Field(default=None, max_length=60)


class Source(BaseModel):
    date: str
    sp: int
    titre: str
    decision: str
    score: float
    # Lien : PDF officiel du PV (résolu par date) OU deep-link vidéo pour un débat
    # filmé (métadonnée) — None si absent.
    url: Optional[str] = None
    # Type de source : "pv" (délibération, défaut) ou "video_conseil" (débat filmé).
    source_type: str = "pv"
    # Lien vers la vidéo de la séance (début) quand elle a été filmée — pour un
    # « ▶ voir la séance » sur une délibération, même sans chapitrage. None sinon.
    video_url: Optional[str] = None


class AnswerResponse(BaseModel):
    answer: str
    sources: list[Source]
