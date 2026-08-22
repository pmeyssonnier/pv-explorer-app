"""Authentification administrateur — un seul compte (voir la décision : pas
plusieurs admins pour l'instant), identifiants dans les variables
d'environnement plutôt qu'une base de données (qui n'existe pas dans ce
backend — tout est lu depuis des fichiers JSON statiques).

Mot de passe : jamais stocké en clair, même en variable d'environnement — on
stocke un hash PBKDF2-HMAC-SHA256 salé (ADMIN_PASSWORD_HASH, calculé une fois
via hash_password ci-dessous) et on compare en temps constant. Pas de bcrypt
(dépendance native à compiler, superflue ici) : PBKDF2 via hashlib suffit et
ne dépend que de la stdlib.

Session : jeton signé "payload_b64.signature_b64" (HMAC-SHA256 avec
ADMIN_JWT_SECRET) — pas chiffré, il ne contient que le nom d'utilisateur et
une expiration, rien de sensible. Pas de librairie JWT (encore une dépendance
en moins) : même principe qu'un JWT HS256, réduit au strict nécessaire pour un
seul rôle.

Pour générer les deux secrets à mettre dans l'environnement (Render) :
    cd backend && python3 -c "from services.auth import hash_password; print(hash_password('VOTRE_MOT_DE_PASSE'))"
    python3 -c "import secrets; print(secrets.token_hex(32))"   # ADMIN_JWT_SECRET
"""
import base64
import hashlib
import hmac
import json
import os
import time

_PBKDF2_ITERATIONS = 600_000  # recommandation OWASP (2023) pour PBKDF2-HMAC-SHA256
SESSION_TTL_S = 24 * 3600


def hash_password(password: str) -> str:
    """Hash à stocker dans ADMIN_PASSWORD_HASH — jamais appelé en production,
    seulement pour générer la valeur de la variable d'environnement."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split("$", 1)
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return hmac.compare_digest(actual, expected)


def verify_admin_credentials(username: str, password: str) -> bool:
    """Lit les identifiants attendus depuis l'environnement à chaque appel
    (comme PINECONE_API_KEY dans pinecone_service.py) plutôt qu'au chargement
    du module — un déploiement sans ADMIN_* configuré ne doit pas empêcher le
    reste de l'app de démarrer, juste désactiver la connexion (retourne False)."""
    expected_username = os.environ.get("ADMIN_USERNAME", "")
    expected_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")
    if not expected_username or not expected_hash:
        return False
    # _verify_password tourne TOUJOURS (coût constant), que le nom d'utilisateur
    # soit correct ou non, pour ne pas laisser deviner par le temps de réponse
    # si un identifiant existe avant même de tester le mot de passe.
    password_ok = _verify_password(password, expected_hash)
    username_ok = hmac.compare_digest(username, expected_username)
    return username_ok and password_ok


def create_session_token(username: str) -> str:
    secret = os.environ.get("ADMIN_JWT_SECRET", "")
    if not secret:
        raise RuntimeError("ADMIN_JWT_SECRET manquante côté serveur")
    payload = json.dumps({"sub": username, "exp": int(time.time()) + SESSION_TTL_S}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload).rstrip(b"=")
    sig = hmac.new(secret.encode(), payload_b64, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return (payload_b64 + b"." + sig_b64).decode()


def verify_session_token(token: str) -> str | None:
    """Nom d'utilisateur si le jeton est valide (signature correcte, non
    expiré), sinon None — jamais d'exception, un jeton malformé/périmé/rejoué
    avec une autre clé doit juste échouer silencieusement (401 côté route)."""
    secret = os.environ.get("ADMIN_JWT_SECRET", "")
    if not secret or not token or "." not in token:
        return None
    payload_b64, _, sig_b64 = token.partition(".")
    try:
        expected_sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
        actual_sig = base64.urlsafe_b64decode(sig_b64 + "==")
    except Exception:
        return None
    if not hmac.compare_digest(expected_sig, actual_sig):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
