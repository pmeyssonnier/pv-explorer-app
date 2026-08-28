"""Routes /admin/* : authentification administrateur (un seul compte, voir
services/auth.py) + intégration d'un nouveau PV uploadé (voir
services/pv_integration.py). Toute route au-delà de login/logout/me se
protège via `require_admin`.
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

import lexique_store
from limiter import limiter
from models.api import AdminLoginRequest, MandatSaveRequest, QuestionEcritePublishRequest, SeancePublishRequest
from services import github_publish, jobs, pv_integration, questions_ecrites_integration
from services.auth import SESSION_TTL_S, create_session_token, verify_admin_credentials, verify_session_token
from services.people import mandats as mandats_store


class LexiqueEntryRequest(BaseModel):
    kind: str
    key: str
    value: str

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
    """Étape 1/2 : démarre l'extraction du PDF uploadé (appelle Claude —
    coûte réellement, d'où le rate limit) en tâche de fond et retourne un
    job_id immédiatement — voir GET .../extract/{job_id}. NE PUBLIE RIEN.

    Ne fait PAS attendre la requête sur toute la durée de l'extraction
    (plusieurs appels Claude séquentiels, potentiellement plusieurs minutes
    sur un PV dense) : le proxy Render coupe la connexion avant la fin,
    ce qui se manifeste côté navigateur comme un échec CORS trompeur (aucune
    réponse à vérifier, pas un vrai problème de politique CORS)."""
    # Lu en sync (pas `await file.read()`) : cette route est un `def` classique,
    # comme le reste du backend — FastAPI l'exécute déjà dans un threadpool.
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="Fichier vide.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 20 Mo).")
    job_id = jobs.start_job(pv_integration.extract_and_preview, raw, file.filename or "upload.pdf")
    return {"job_id": job_id}


@router.get("/seances/extract/{job_id}")
def extract_status(job_id: str, username: str = Depends(require_admin)):
    return _job_status(job_id)


@router.post("/seances/publish")
@limiter.limit("10/hour")
def publish_seance(request: Request, body: SeancePublishRequest, username: str = Depends(require_admin)):
    """Étape 2/2 : démarre la publication (fusion, commit GitHub, réindexation
    Pinecone) en tâche de fond — mêmes raisons que /extract : un commit sur un
    fichier de plusieurs Mo + un upsert Pinecone (avec retry pouvant attendre
    jusqu'à 65s sur un 429) dépassent facilement le délai toléré par Render.
    Reçoit la séance telle que renvoyée par /admin/seances/extract (l'admin en
    a vu l'aperçu)."""
    job_id = jobs.start_job(pv_integration.publish_seance, body.seance, body.source_url)
    return {"job_id": job_id}


@router.get("/seances/publish/{job_id}")
def publish_status(job_id: str, username: str = Depends(require_admin)):
    return _job_status(job_id)


@router.post("/questions-ecrites/extract")
@limiter.limit("10/hour")
def extract_question_ecrite(
    request: Request, file: UploadFile = File(...), username: str = Depends(require_admin),
):
    """Étape 1/2 : même flux que /seances/extract (voir sa docstring pour le
    raisonnement job_id/tâche de fond) appliqué à une question écrite —
    document plus court, donc plus rapide, mais même risque de coupure
    proxy sur un appel Claude, d'où le même sondage côté front."""
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="Fichier vide.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 20 Mo).")
    job_id = jobs.start_job(
        questions_ecrites_integration.extract_and_preview, raw, file.filename or "upload.pdf",
    )
    return {"job_id": job_id}


@router.get("/questions-ecrites/extract/{job_id}")
def extract_question_ecrite_status(job_id: str, username: str = Depends(require_admin)):
    return _job_status(job_id)


@router.post("/questions-ecrites/publish")
@limiter.limit("10/hour")
def publish_question_ecrite(
    request: Request, body: QuestionEcritePublishRequest, username: str = Depends(require_admin),
):
    """Étape 2/2 : reçoit la question telle que renvoyée par
    /admin/questions-ecrites/extract (l'admin en a vu l'aperçu)."""
    job_id = jobs.start_job(
        questions_ecrites_integration.publish_question, body.question, body.source_url,
    )
    return {"job_id": job_id}


@router.get("/questions-ecrites/publish/{job_id}")
def publish_question_ecrite_status(job_id: str, username: str = Depends(require_admin)):
    return _job_status(job_id)


def _job_status(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Tâche inconnue ou expirée.")
    if job["status"] == "pending":
        return {"status": "pending", "progress": job.get("progress")}
    if job["status"] == "error":
        raise HTTPException(status_code=job["code"], detail=job["detail"])
    return {"status": "done", **job["result"]}


# ── LEXIQUE éditable (synonymes/associations + glossaire) ────────────────────
# Enrichi à chaud par l'admin (commande « //lex … » côté chat, réservée à une
# session admin) : l'ajout est écrit localement (effet immédiat sur l'instance)
# puis commité dans le dépôt (persistance + redéploiement). Voir lexique_store.
@router.get("/lexique")
def get_lexique(username: str = Depends(require_admin)):
    return lexique_store.load()


@router.post("/lexique")
@limiter.limit("60/hour")
def add_lexique_entry(request: Request, body: LexiqueEntryRequest, username: str = Depends(require_admin)):
    """Ajoute une entrée au lexique. `kind` ∈ theme/decision/alias/nom/def/
    retrait/report/approbation/rejet (voir lexique_store._KINDS)."""
    try:
        data = lexique_store.add_entry(body.kind, body.key, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        github_publish.commit_file(
            "backend/lexique.json", lexique_store.as_json(data),
            f"lexique: {body.kind} « {body.key} » (via admin)",
        )
        committed = True
    except Exception:
        # L'écriture locale a réussi (effet immédiat) mais le commit distant a
        # échoué : on le signale sans perdre l'ajout côté instance courante.
        committed = False
    return {"status": "done", "kind": body.kind, "committed": committed, "lexique": data}


# ── MANDATS déclaratifs éditables (conseiller·ère/échevin·e/bourgmestre par
# plage de dates — voir services/people/mandats.py) ──────────────────────────
# Même mécanique que le lexique ci-dessus : écriture locale (effet immédiat
# sur l'instance, via le cache par mtime de services.people.mandats) puis
# commit dans le dépôt (persistance + redéploiement — voir render.yaml,
# buildFilter backend/**).
@router.get("/mandats")
def get_mandats(username: str = Depends(require_admin)):
    return {"mandats": mandats_store.list_mandats()}


@router.post("/mandats")
@limiter.limit("60/hour")
def save_mandat(request: Request, body: MandatSaveRequest, username: str = Depends(require_admin)):
    try:
        entry = mandats_store.save_mandat(
            body.nom, body.conseiller_communal, body.echevin, body.bourgmestre, body.statut,
            nom_original=body.nom_original,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        github_publish.commit_file(
            "backend/elus_mandats.json", mandats_store.as_json(),
            f"data: mandat de {entry['nom']} (via panneau admin)",
        )
        committed = True
    except Exception:
        # L'écriture locale a réussi (effet immédiat) mais le commit distant a
        # échoué : on le signale sans perdre la modification côté instance courante.
        committed = False
    return {"status": "done", "committed": committed, "mandat": entry}


@router.delete("/mandats")
@limiter.limit("60/hour")
def delete_mandat(request: Request, nom: str, username: str = Depends(require_admin)):
    try:
        entry = mandats_store.delete_mandat(nom)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        github_publish.commit_file(
            "backend/elus_mandats.json", mandats_store.as_json(),
            f"data: suppression du mandat de {entry['nom']} (via panneau admin)",
        )
        committed = True
    except Exception:
        committed = False
    return {"status": "done", "committed": committed, "mandat": entry}
