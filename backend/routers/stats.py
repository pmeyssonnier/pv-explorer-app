"""Routes /stats et /trend : agrégations lues depuis la base JSON des PV.

Les fonctions de services.statistics sont pures et lèvent des exceptions
Python ; ce routeur les traduit en réponses HTTP (404 / 400).
"""
from fastapi import APIRouter, HTTPException, Request, Query

from limiter import limiter
from services import statistics

router = APIRouter()


@router.get("/stats")
@limiter.limit("30/minute")
def stats(request: Request):
    """Statistiques globales calculées depuis la base JSON des PV."""
    try:
        db = statistics.load_db()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Fichier de stats non disponible")
    return statistics.compute_stats(db)


@router.get("/trend")
@limiter.limit("30/minute")
def trend(request: Request, theme: str = Query(..., min_length=2, max_length=60)):
    """Évolution d'un thème : montants agrégés par année (balayage exhaustif)."""
    try:
        db = statistics.load_db()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Données non disponibles")
    try:
        return statistics.compute_trend(db, theme)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
