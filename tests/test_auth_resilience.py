"""Unit tests for the transient-database wrapper behind login/signup/auth-me.

A cold database must cost the user one short retry — and if it is still cold,
an honest 503 ("Warming up — try again in a few seconds"), never an unhandled
500 that the login screen cannot explain.

    .venv/bin/python -m pytest tests/test_auth_resilience.py -q
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", os.path.join(tempfile.gettempdir(), "aether-resilience.db"))
os.environ.setdefault("APP_SECRET", "resilience-test-secret-0123456789abcdef")
os.environ.pop("VERCEL", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.db.resilience import (  # noqa: E402
    DB_WARMING_DETAIL,
    auth_db_call,
    db_retry,
    is_transient_db_error,
)
from app.main import app  # noqa: E402
from app.security import create_access_token  # noqa: E402

client = TestClient(app)


# ------------------------------------------------------------ classification

def test_transient_classification():
    assert is_transient_db_error(sqlite3.OperationalError("database is locked"))
    assert is_transient_db_error(sqlite3.OperationalError("SQL logic error"))
    assert is_transient_db_error(RuntimeError("Turso error: stream closed"))
    assert is_transient_db_error(TimeoutError("read timed out"))
    assert not is_transient_db_error(ValueError("no such column: nope"))
    assert not is_transient_db_error(HTTPException(status_code=401, detail="nope"))


# ------------------------------------------------------------------ wrapper

def test_transient_error_is_retried_once_and_succeeds():
    calls: list[int] = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    started = time.monotonic()
    assert db_retry(flaky, label="test") == "ok"
    assert len(calls) == 2, "the statement must be retried exactly once"
    assert time.monotonic() - started >= 0.2, "the retry must wait ~250 ms"


def test_persistent_transient_error_becomes_503():
    def dead():
        raise sqlite3.OperationalError("SQL logic error")

    with pytest.raises(HTTPException) as exc:
        auth_db_call(dead, label="test")
    assert exc.value.status_code == 503
    assert exc.value.detail == DB_WARMING_DETAIL


def test_non_transient_error_stays_visible():
    def broken():
        raise ValueError("no such column: nope")

    with pytest.raises(ValueError):
        auth_db_call(broken, label="test")


def test_deliberate_http_errors_pass_through():
    def denied():
        raise HTTPException(status_code=401, detail="Invalid credentials")

    with pytest.raises(HTTPException) as exc:
        auth_db_call(denied, label="test")
    assert exc.value.status_code == 401


# -------------------------------------------------------------- HTTP surface

def test_login_survives_one_transient_failure(monkeypatch):
    """One hiccup must not reach the user as an error at all."""
    import app.routers.auth as auth_router

    state = {"failed": False}

    def flaky(email):
        if not state["failed"]:
            state["failed"] = True
            raise sqlite3.OperationalError("database is locked")
        return None                      # retry answers honestly: no such user

    monkeypatch.setattr(auth_router, "get_user_by_email", flaky)
    r = client.post("/api/auth/login",
                    json={"email": "nobody@example.com", "password": "secret1"})
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "No account with this email yet — create one below"


def test_login_returns_503_when_the_database_stays_cold(monkeypatch):
    import app.routers.auth as auth_router

    def cold(email):
        raise sqlite3.OperationalError("Turso error: stream closed")

    monkeypatch.setattr(auth_router, "get_user_by_email", cold)
    r = client.post("/api/auth/login",
                    json={"email": "cold@example.com", "password": "secret1"})
    assert r.status_code == 503, r.text
    assert r.json() == {"detail": DB_WARMING_DETAIL}


def test_register_returns_503_when_the_database_stays_cold(monkeypatch):
    import app.routers.auth as auth_router

    def cold(email):
        raise sqlite3.OperationalError("SQL logic error: interrupted")

    monkeypatch.setattr(auth_router, "get_user_by_email", cold)
    r = client.post("/api/auth/register",
                    json={"email": "cold-register@example.com", "password": "secret1"})
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == DB_WARMING_DETAIL


def test_auth_me_returns_503_when_the_database_stays_cold(monkeypatch):
    import app.deps as deps

    def cold(email):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(deps, "get_user_by_email", cold)
    token = create_access_token({"sub": "ghost@example.com", "id": 1})
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == DB_WARMING_DETAIL
