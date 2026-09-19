from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.database import get_user_by_email
from app.db.resilience import auth_db_call
from app.security import decode_access_token

# auto_error=False so we can fall back to the session cookie ourselves.
security_scheme = HTTPBearer(auto_error=False)

SESSION_COOKIE = "aether_token"


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
):
    """Accept the JWT via Authorization header OR httpOnly session cookie.

    Some hosting proxies strip or mangle Authorization headers; the cookie
    path keeps browser sessions working in those environments.
    """
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(SESSION_COOKIE) or ""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Retry once on a transient database hiccup; if the database is still cold
    # this answers 503 ("Warming up...") rather than a misleading 401 that
    # would make a valid session look expired.
    user = auth_db_call(lambda: get_user_by_email(payload["sub"]), label="auth me")
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
