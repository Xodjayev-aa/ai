import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr

from app.ai.config import MAX_PASSWORD_BYTES
from app.database import create_user, get_user_by_email
from app.deps import SESSION_COOKIE, get_current_user
from app.security import create_access_token, hash_password, verify_password

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


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=TOKEN_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )


@router.post("/register")
def register(payload: AuthPayload, response: Response):
    email = payload.email.lower()
    if get_user_by_email(email):
        raise HTTPException(status_code=400, detail="Email already registered")
    _validate_password(payload.password)
    owner_email = os.getenv("OWNER_EMAIL", "").strip().lower()
    is_admin = bool(owner_email) and email == owner_email
    hashed = hash_password(payload.password)
    user_id = create_user(email, hashed, is_admin=is_admin)
    token = create_access_token({"sub": email, "id": user_id})
    _set_session_cookie(response, token)
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
    user = get_user_by_email(email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        _record_failure(identity)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user["email"], "id": user["id"]})
    _set_session_cookie(response, token)
    return {"access_token": token, "token_type": "bearer",
            "is_admin": bool(user["is_admin"])}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"status": "success"}


@router.get("/me")
def me(user=Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"],
            "is_admin": bool(user["is_admin"])}
