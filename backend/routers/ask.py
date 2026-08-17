"""Route /ask : question ouverte → recherche RAG + réponse Claude citée."""
from fastapi import APIRouter, Request

from limiter import limiter
from models.api import QuestionRequest, AnswerResponse
from services import rag

router = APIRouter()


@router.post("/ask", response_model=AnswerResponse)
@limiter.limit("10/minute;100/day")
def ask(request: Request, req: QuestionRequest):
    """Délègue toute la logique à services.rag.answer (recherche + génération)."""
    return rag.answer(req.question, req.commune)
