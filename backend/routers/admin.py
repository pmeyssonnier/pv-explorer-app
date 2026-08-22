"""Routes /admin/* : authentification administrateur (un seul compte, voir
services/auth.py). Pour l'instant, seulement connexion/déconnexion/statut —
fondation pour les futures actions d'administration (ex. intégration d'un
nouveau PV uploadé), qui se protégeront toutes via `require_admin`.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from limiter import limiter
from models.api import AdminLoginRequest
from services.auth import SESSION_TTL_S, create_session_token, verify_admin_credentials, verify_session_token

router = APIRouter(prefix="/admin", tags=["Admin"])

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
