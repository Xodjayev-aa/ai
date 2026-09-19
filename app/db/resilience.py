"""Transient-database resilience for the auth-critical paths.

A cold Turso instance (or a serverless function that just woke up) can fail the
*first* query of a request — "SQL logic error", a dropped HTTP/2 stream, a
timeout — even though the very same database answers fine a moment later. That
is exactly the failure users see as "login is broken": the query raises, the
route turns into an unhandled 500, and the login screen shows nothing useful.

This module keeps that class of failure out of the user's face:

    auth_db_call(call)
        1. runs `call()`
        2. on a *transient* database error, waits ~250 ms and retries ONCE
        3. still failing -> HTTP 503 with a clean JSON detail
           ({"detail": "Warming up — try again in a few seconds"})

Anything that is not transient (a real SQL bug, a constraint violation) is
re-raised untouched, so bugs stay visible instead of being masked as warm-up.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Callable, TypeVar

from fastapi import HTTPException

log = logging.getLogger("aether.db")

T = TypeVar("T")

DB_WARMING_DETAIL = "Warming up — try again in a few seconds"

# One retry, ~250 ms apart: enough to ride out a cold start without making a
# genuinely broken database feel slow to the user.
DB_RETRY_ATTEMPTS = 2
DB_RETRY_DELAY = 0.25

# Substrings (lower-cased) that mark an error as "the database hiccuped",
# not "the query is wrong". Covers the real-world shapes we see:
#   - sqlite3.OperationalError ("database is locked", "unable to open ...")
#   - Turso over HTTP: "SQL logic error", 5xx from the edge, dropped streams
#   - httpx transport errors (connect/read timeouts, protocol errors)
_TRANSIENT_MARKERS = (
    "operationalerror",
    "sql logic error",
    "database is locked",
    "database table is locked",
    "unable to open database",
    "disk i/o error",
    "cannot start a transaction",
    "turso error",
    "turso unexpected result",
    "connecterror",
    "connecttimeout",
    "readtimeout",
    "writetimeout",
    "pooltimeout",
    "remoteprotocolerror",
    "readerror",
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection closed",
    "server disconnected",
    "broken pipe",
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "too many requests",
)


def is_transient_db_error(exc: BaseException) -> bool:
    """True when retrying the same statement has a real chance of working."""
    if isinstance(exc, HTTPException):          # deliberate control flow
        return False
    if isinstance(exc, sqlite3.OperationalError):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    # httpx is imported lazily elsewhere in the engine; never hard-require it.
    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
    except Exception:  # pragma: no cover - httpx is a hard dependency in practice
        pass

    message = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in message for marker in _TRANSIENT_MARKERS)


def db_retry(call: Callable[[], T], *, attempts: int = DB_RETRY_ATTEMPTS,
             delay: float = DB_RETRY_DELAY, label: str = "db") -> T:
    """Run `call`, retrying once after `delay` on a transient database error.

    Non-transient errors are raised immediately (no pointless 250 ms wait), and
    the last error is always re-raised so callers can decide what to report.
    """
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — classified below
            transient = is_transient_db_error(exc)
            if not transient or attempt >= attempts:
                raise
            log.warning("%s: transient database error (attempt %d/%d), retrying in %.0f ms — %s",
                        label, attempt, attempts, delay * 1000, f"{type(exc).__name__}: {exc}"[:200])
            time.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def auth_db_call(call: Callable[[], T], *, label: str = "auth") -> T:
    """DB access on a login/signup/me path: retry once, then answer 503.

    Never lets a transient database failure escape as an unhandled 500 — the
    client shows the detail verbatim and can retry immediately.
    """
    try:
        return db_retry(call, label=label)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — reported honestly below
        if is_transient_db_error(exc):
            log.warning("%s: database still unavailable after retry — returning 503 (%s)",
                        label, f"{type(exc).__name__}: {exc}"[:200])
            raise HTTPException(status_code=503, detail=DB_WARMING_DETAIL) from exc
        raise


def warmup_probe() -> dict[str, Any]:
    """Cheap, non-blocking description of the warm-up state (used by /api/ai/status)."""
    from app.db import engine  # local import: engine imports this module

    return {
        "turso_configured": engine.TURSO_CONFIGURED,
        "warm": engine.turso_warm(),
        "warmer_started": engine.warmer_started(),
    }
