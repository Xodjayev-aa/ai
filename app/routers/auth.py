import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr

from app.ai.config import MAX_PASSWORD_BYTES
from app.database import create_user, get_user_by_email, update_password
from app.db.resilience import auth_db_call
from app.deps import SESSION_COOKIE, get_current_user
from app.security import create_access_token, hash_password, verify_password

# Every user lookup / write below goes through auth_db_call(): a cold or
# hiccuping database costs one 250 ms retry, and a database that is still
# unavailable answers 503 {"detail": "Warming up — try again in a few seconds"}
# instead of an unhandled 500 on the login screen.

router = APIRouter(prefix="/api/auth", tags=["auth"])

TOKEN_MAX_AGE = 604800  # 7 days, matches security.TOKEN_EXPIRY

# In-memory login throttle: max failures per identity, sliding window.
# (Per serverless instance — still raises the bar substantially against
# credential stuffing; add Redis/Upstash if you need global enforcement.)
_MAX_FAILURES = 8
_WINDOW_SECONDS = 900  # 15 minutes
_failures: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else
            request.client.host if request.client else "unknown")


def _throttled(identity: str) -> bool:
    now = time.time()
    hits = [t for t in _failures.get(identity, []) if now - t < _WINDOW_SECONDS]
    _failures[identity] = hits
    return len(hits) >= _MAX_FAILURES


def _record_failure(identity: str) -> None:
    _failures.setdefault(identity, []).append(time.time())


def _validate_password(password: str) -> None:
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Password too long (max {MAX_PASSWORD_BYTES} bytes).",
        )
    if len(password) < 6:
        raise HTTPException(status_code=400,
                            detail="Password must be at least 6 characters.")


class AuthPayload(BaseModel):
    email: EmailStr
    password: str


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    """Adaptive cookie policy:
    - HTTPS (Vercel, iframe preview proxies): SameSite=None + Secure so the
      cookie also works when the app is embedded in a cross-site iframe.
    - Plain HTTP localhost: SameSite=Lax (Secure cookies are rejected there).
    Both contexts are first-party on Vercel, so this covers every deployment.
    """
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    is_https = request.url.scheme == "https" or forwarded == "https"
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=TOKEN_MAX_AGE,
        httponly=True,
        samesite="none" if is_https else "lax",
        secure=is_https,
        path="/",
    )


@router.post("/register")
def register(payload: AuthPayload, request: Request, response: Response):
    email = payload.email.lower()
    if auth_db_call(lambda: get_user_by_email(email), label="register lookup"):
        raise HTTPException(status_code=400, detail="Email already registered")
    _validate_password(payload.password)
    owner_email = os.getenv("OWNER_EMAIL", "").strip().lower()
    is_admin = bool(owner_email) and email == owner_email
    hashed = hash_password(payload.password)
    user_id = auth_db_call(lambda: create_user(email, hashed, is_admin=is_admin),
                           label="register insert")
    token = create_access_token({"sub": email, "id": user_id})
    _set_session_cookie(request, response, token)
    return {"access_token": token, "token_type": "bearer", "is_admin": is_admin}


@router.post("/login")
def login(payload: AuthPayload, request: Request, response: Response):
    email = payload.email.lower()
    identity = f"{email}|{_client_ip(request)}"
    if _throttled(identity):
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again in ~15 minutes.",
        )
    user = auth_db_call(lambda: get_user_by_email(email), label="login lookup")
    if not user or not verify_password(payload.password, user["password_hash"]):
        _record_failure(identity)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user["email"], "id": user["id"]})
    _set_session_cookie(request, response, token)
    return {"access_token": token, "token_type": "bearer",
            "is_admin": bool(user["is_admin"])}


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.post("/password")
def change_password(body: PasswordChange,
                    user=Depends(get_current_user)):
    """Change password for the logged-in user (requires current password)."""
    if not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(status_code=401,
                            detail="Current password is incorrect.")
    _validate_password(body.new_password)
    auth_db_call(lambda: update_password(user["id"], hash_password(body.new_password)),
                 label="password update")
    return {"status": "success",
            "message": "Password updated. Use it next time you sign in."}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"status": "success"}


@router.get("/me")
def me(user=Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"],
            "is_admin": bool(user["is_admin"])}
