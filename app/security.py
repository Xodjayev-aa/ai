import hashlib
import os
import secrets
import time

import bcrypt
import jwt

# ── session signing key ─────────────────────────────────────────────────────
# A hard-coded fallback secret would let anyone mint a token for any account on
# every deployment that forgets to set APP_SECRET, so there is no literal
# fallback. Preference order:
#   1. APP_SECRET                       — explicit, survives restarts/redeploys
#   2. derived from TURSO_AUTH_TOKEN    — stable across instances, never public
#   3. random per process               — safe, but sessions reset on restart
_explicit_secret = (os.getenv("APP_SECRET") or "").strip()
_turso_seed = (os.getenv("TURSO_AUTH_TOKEN") or "").strip()

if _explicit_secret:
    SECRET_KEY = _explicit_secret
    SECRET_SOURCE = "APP_SECRET"
    SECRET_WARNING = ""
elif _turso_seed:
    SECRET_KEY = hashlib.sha256(f"aether-session-v1|{_turso_seed}".encode()).hexdigest()
    SECRET_SOURCE = "derived from TURSO_AUTH_TOKEN"
    SECRET_WARNING = ("Sessions are signed with a key derived from the Turso token. "
                      "Set APP_SECRET to control session lifetime explicitly.")
else:
    SECRET_KEY = secrets.token_urlsafe(48)
    SECRET_SOURCE = "ephemeral (this process only)"
    SECRET_WARNING = ("No APP_SECRET is configured, so logins reset whenever the "
                      "server restarts. Set APP_SECRET to keep people signed in.")

ALGORITHM = "HS256"
TOKEN_EXPIRY = 604800  # 7 days


def secret_status() -> dict:
    """Exposed through /api/ai/status so the UI can be honest about sessions."""
    return {"source": SECRET_SOURCE, "warning": SECRET_WARNING,
            "configured": bool(_explicit_secret)}

def hash_password(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def create_access_token(data: dict) -> str:
    payload = data.copy()
    payload.update({"exp": time.time() + TOKEN_EXPIRY})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
