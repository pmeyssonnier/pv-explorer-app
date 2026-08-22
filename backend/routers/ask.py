"""Route /ask : question ouverte → recherche RAG + réponse Claude citée."""
from fastapi import APIRouter, Request

from limiter import limiter
from models.api import QuestionRequest, AnswerResponse
from services import rag

router = APIRouter(tags=["RAG"])


@router.post(
    "/ask",
    response_model=AnswerResponse,
    summary="Poser une question aux PV",
    response_description="Réponse citée avec ses sources",
)
@limiter.limit("10/minute;100/day")
def ask(request: Request, req: QuestionRequest):
    """Délègue toute la logique à services.rag.answer (recherche + génération).
    Les overrides du menu Options (top_k, max_sources, score_min, model) sont
    transmis et re-bornés dans answer()."""
    return rag.answer(
        req.question, req.commune,
        top_k=req.top_k, max_sources=req.max_sources,
        score_min=req.score_min, model=req.model,
    )
