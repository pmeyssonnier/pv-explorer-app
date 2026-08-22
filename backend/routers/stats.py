"""Routes /stats et /trend : agrégations lues depuis la base JSON des PV.

Les fonctions de services.statistics sont pures et lèvent des exceptions
Python ; ce routeur les traduit en réponses HTTP (404 / 400).
"""
from fastapi import APIRouter, HTTPException, Request, Query

from limiter import limiter
from services import statistics, elus

router = APIRouter(tags=["Statistiques"])


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


@router.get("/elus")
@limiter.limit("30/minute")
def elus_list(request: Request):
    """Liste des élu·e·s (nom, rôle, nombre d'interventions déposées/répondues).
    Alimente le sélecteur « Interventions par élu·e ». Agrégation déterministe
    depuis la base JSON des PV — indépendante de la formulation (contrairement
    au chat sémantique)."""
    try:
        return {"elus": elus.elus_list()}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Données non disponibles")


@router.get("/elu/{key}")
@limiter.limit("60/minute")
def elu_detail(request: Request, key: str):
    """Détail d'un·e élu·e : interventions déposées (questions orales, demandes,
    motions, débats filmés) et réponses en séance (activité de Collège)."""
    try:
        detail = elus.elu_detail(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Données non disponibles")
    if detail is None:
        raise HTTPException(status_code=404, detail="Élu·e inconnu·e")
    return detail


@router.get("/seances")
@limiter.limit("30/minute")
def seances_list(request: Request):
    """Liste des séances (PV), la plus récente en premier. Alimente la
    navigation par année de l'onglet « Séances »."""
    try:
        return {"seances": elus.seances_list()}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Données non disponibles")


@router.get("/seance/{date}")
@limiter.limit("60/minute")
def seance_detail(request: Request, date: str):
    """Détail d'une séance : tous les points du PV (objet, demandeur·se,
    répondant·e, lien PV, lien vidéo précis si un chapitre correspondant
    existe)."""
    try:
        detail = elus.seance_detail(date)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Données non disponibles")
    if detail is None:
        raise HTTPException(status_code=404, detail="Séance inconnue")
    return detail
