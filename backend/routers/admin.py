"""Routes /admin/* : authentification administrateur (un seul compte, voir
services/auth.py) + intégration d'un nouveau PV uploadé (voir
services/pv_integration.py). Toute route au-delà de login/logout/me se
protège via `require_admin`.
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

from limiter import limiter
from models.api import AdminLoginRequest, SeancePublishRequest
from services import pv_integration
from services.auth import SESSION_TTL_S, create_session_token, verify_admin_credentials, verify_session_token

router = APIRouter(prefix="/admin", tags=["Admin"])

# Garde-fou anti-abus : un upload malformé/énorme ne doit pas être lu
# entièrement en mémoire avant d'être rejeté (aucun PV Schaerbeek ne dépasse
# quelques Mo — 20 Mo laisse largement la marge pour un PV dense).
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

SESSION_COOKIE = "pv_admin_session"

# Cookie cross-site (frontend Vercel ↔ backend Render, domaines différents) :
# SameSite=None exige Secure — imposé par défaut. Pour les tests locaux en
# HTTP simple (pas de TLS), ADMIN_COOKIE_SECURE=false bascule sur
# SameSite=Lax (fonctionne tant que front/back local partagent le même hôte,
# ex. localhost:5500 ↔ localhost:8000 — voir tests/Playwright). JAMAIS mettre
# ADMIN_COOKIE_SECURE=false en production : le cookie de session partirait
# alors en clair. Lu à chaque requête (pas au chargement du module) : comme
# les autres réglages par env (voir config.py), et testable sans recharger.
def _cookie_flags() -> tuple[bool, str]:
    secure = os.environ.get("ADMIN_COOKIE_SECURE", "true").strip().lower() != "false"
    return secure, ("none" if secure else "lax")


def require_admin(request: Request) -> str:
    """Dépendance FastAPI à réutiliser sur toute future route d'administration."""
    token = request.cookies.get(SESSION_COOKIE)
    username = verify_session_token(token) if token else None
    if not username:
        raise HTTPException(status_code=401, detail="Authentification requise")
    return username


@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, body: AdminLoginRequest, response: Response):
    if not verify_admin_credentials(body.username, body.password):
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    try:
        token = create_session_token(body.username)
    except RuntimeError:
        # Compte configuré (ADMIN_USERNAME/HASH) mais pas ADMIN_JWT_SECRET :
        # erreur de configuration serveur, pas un mauvais mot de passe.
        raise HTTPException(status_code=500, detail="Configuration serveur incomplète")
    secure, samesite = _cookie_flags()
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, secure=secure, samesite=samesite,
        max_age=SESSION_TTL_S, path="/",
    )
    return {"ok": True, "username": body.username}


@router.post("/logout")
def logout(response: Response):
    secure, samesite = _cookie_flags()
    response.delete_cookie(SESSION_COOKIE, path="/", samesite=samesite, secure=secure)
    return {"ok": True}


@router.get("/me")
def me(username: str = Depends(require_admin)):
    return {"authenticated": True, "username": username}


@router.post("/seances/extract")
@limiter.limit("10/hour")
def extract_seance(
    request: Request, file: UploadFile = File(...), username: str = Depends(require_admin),
):
    """Étape 1/2 : extrait la structure de la séance depuis le PDF uploadé
    (appelle Claude — coûte réellement, d'où le rate limit) et prévisualise la
    fusion. NE PUBLIE RIEN — voir /admin/seances/publish."""
    # Lu en sync (pas `await file.read()`) : cette route est un `def` classique,
    # comme le reste du backend — FastAPI l'exécute déjà dans un threadpool.
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="Fichier vide.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 20 Mo).")
    try:
        seance_struct = pv_integration.extract_from_upload(raw, file.filename or "upload.pdf")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        # ex. ANTHROPIC_API_KEY manquante côté serveur.
        raise HTTPException(status_code=500, detail=str(e))
    return {"seance": seance_struct, "preview": pv_integration.preview_merge(seance_struct)}


@router.post("/seances/publish")
@limiter.limit("10/hour")
def publish_seance(request: Request, body: SeancePublishRequest, username: str = Depends(require_admin)):
    """Étape 2/2 : reçoit la séance telle que renvoyée par /admin/seances/
    extract (l'admin en a vu l'aperçu), fusionne pour de vrai, committe sur
    GitHub et réindexe dans Pinecone."""
    try:
        return pv_integration.publish_seance(body.seance, body.source_url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        # ex. GITHUB_TOKEN/PINECONE_API_KEY manquante côté serveur.
        raise HTTPException(status_code=500, detail=str(e))
