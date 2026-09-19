"""Dual-mode database engine.

- Local: plain SQLite file (DATABASE_URL, default ./aether.db)
- Vercel / cloud: Turso hosted SQLite over its HTTP v2 pipeline API
  (TURSO_DATABASE_URL + TURSO_AUTH_TOKEN). Same SQL, no driver deps.

Public API:
    query(sql, params)            -> list[dict]           (SELECT)
    execute(sql, params)          -> {"affected": int, "last_insert_rowid": ...}
    execute_many([(sql, params)]) -> None                 (batch, no results)
    init_db()                     -> creates schema
"""

import base64
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Iterable, Sequence

log = logging.getLogger("aether.db")

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

TURSO_URL = (os.getenv("TURSO_DATABASE_URL") or "").strip().rstrip("/")
TURSO_TOKEN = (os.getenv("TURSO_AUTH_TOKEN") or "").strip()
TURSO_CONFIGURED = bool(TURSO_URL and TURSO_TOKEN)
ON_VERCEL = bool(os.getenv("VERCEL"))

if TURSO_URL and TURSO_URL.startswith("libsql://"):
    # Accept libsql:// URLs too and convert to HTTPS for the HTTP API.
    TURSO_URL = "https://" + TURSO_URL[len("libsql://"):]

# Declared before the branch: the moment Turso *is* configured this must not
# be left undefined, or importing the engine raises NameError and the whole
# deployment 500s (the exact opposite of what enabling persistence should do).
_partial_turso = ""

if TURSO_CONFIGURED:
    PIPELINE_URL = TURSO_URL + "/v2/pipeline"
    MODE = "turso"
else:
    PIPELINE_URL = ""
    MODE = "sqlite"
    if TURSO_URL and not TURSO_TOKEN:
        _partial_turso = ("TURSO_DATABASE_URL is set but TURSO_AUTH_TOKEN is "
                          "missing — using temporary storage.")

SQLITE_PATH = (
    os.getenv("DATABASE_URL", "/tmp/aether.db" if os.getenv("VERCEL") else "aether.db")
    .replace("sqlite:///", "")
    .replace("sqlite://", "")
)

_write_lock = threading.Lock()

# --------------------------------------------------------------------------
# Runtime health
# --------------------------------------------------------------------------
# A misconfigured Turso (wrong token, typo'd URL) must never turn the whole app
# into a 500 machine: we detect it on the first query, fall back to local
# SQLite for the lifetime of this instance, and report an honest warning that
# the UI shows as a banner.
_turso_failures = 0
_turso_ever_worked = False
_turso_error = ""
_db_warning = _partial_turso

# Schema state (owned by ensure_schema() further down): declared here because
# status() reports it.
_schema_ready = False
_schema_building = False
_schema_error = ""


def _one_line(exc: Exception, limit: int = 160) -> str:
    """A single-line, UI-safe description of an error."""
    return " ".join(f"{type(exc).__name__}: {exc}".split())[:limit]


def _record_turso_error(exc: Exception) -> None:
    global _turso_error
    _turso_error = _one_line(exc, 300)


def _is_transient(exc: Exception) -> bool:
    """Lazy import: the classifier lives with the auth retry helper."""
    try:
        from app.db.resilience import is_transient_db_error
    except Exception:  # pragma: no cover - defensive, fastapi is a hard dep
        return False
    return is_transient_db_error(exc)


def _is_recoverable_sql_error(exc: Exception) -> bool:
    """A migration-level collision (e.g. 'duplicate column name') proves the
    database *answered* — the schema simply already exists. It must never
    count as a Turso outage and never be reported as the 'turso error'."""
    return "duplicate column name" in str(exc).lower()


def _looks_like_misconfiguration(exc: Exception) -> bool:
    """True when the failure says the *configuration* is wrong (bad token,
    typo'd URL) rather than the database hiccuping. Only then may the
    user-facing banner point at TURSO_DATABASE_URL / TURSO_AUTH_TOKEN."""
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            try:
                code = exc.response.status_code
            except Exception:  # pragma: no cover
                code = 0
            return code in (401, 403, 404)
    except Exception:  # pragma: no cover
        pass
    message = str(exc).lower()
    markers = (
        "401 unauthorized",
        "403 forbidden",
        "404 not found",
        "name or service not known",          # DNS: typo'd URL
        "failed to resolve",
        "temporary failure in name resolution",
        "connection refused",                 # host answers nothing
    )
    return any(marker in message for marker in markers)


def _degrade_to_sqlite(exc: Exception) -> None:
    """Fall back to temporary storage and tell the user the truth.

    A hiccup (cold database, brief outage, migration race) gets the
    'retrying automatically' banner: a background probe flips the engine back
    to Turso the moment it heals — no redeploy required. Only a genuinely
    misconfigured Turso (bad URL/token) points at the environment variables.
    """
    global MODE, _db_warning
    _record_turso_error(exc)
    if MODE != "turso":
        return
    MODE = "sqlite"
    if _looks_like_misconfiguration(exc):
        _db_warning = (
            "Turso is misconfigured (TURSO_DATABASE_URL / TURSO_AUTH_TOKEN): "
            f"{_one_line(exc)} — data is stored temporarily until fixed."
        )
    else:
        _db_warning = (
            f"Database hiccup: {_one_line(exc)} — retrying automatically; "
            "nothing you need to change."
        )
    log.warning("database degraded to temporary storage: %s", _one_line(exc))
    start_recovery()


def _turso_guard(call):
    """Run a Turso call; degrade to SQLite when Turso never works or keeps failing.

    A single *transient* failure right after a cold start is not a broken
    configuration — the warm-up thread and the per-request retry can still
    rescue the caller, so we only fall back after real repetition (three
    failures in a row). A hard, non-transient error (bad token, typo'd URL)
    still degrades immediately, exactly as before.

    A migration-level SQL collision (duplicate column) is not a failure at
    all: the database processed the statement, which is proof of health. It
    resets the outage counter and is never recorded as the 'turso error'.
    (Counting these was exactly the bug behind the false 'Turso unreachable'
    degrade on an already-migrated database.)
    """
    global _turso_failures, _turso_ever_worked
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001 — any failure counts
        if _is_recoverable_sql_error(exc):
            _turso_failures = 0
            raise
        _turso_failures += 1
        _record_turso_error(exc)
        if _turso_failures >= 3 or (not _turso_ever_worked and not _is_transient(exc)):
            _degrade_to_sqlite(exc)
        raise
    _turso_failures = 0
    _turso_ever_worked = True
    return result


def is_persistent() -> bool:
    """True when data survives a cold start of the serverless instance."""
    return MODE == "turso" or not ON_VERCEL


# --------------------------------------------------------------------------
# Turso warm-up
# --------------------------------------------------------------------------
# A cold Turso edge can take a few seconds to answer its first query. When that
# first query happens to be "SELECT * FROM users WHERE email = ?" the login
# fails with a 500 for no good reason. So on startup (and again from the first
# request after boot, because serverless runtimes freeze idle instances) we
# probe with a trivial statement on a daemon thread — never on the request path.

WARMUP_DELAYS = (0, 5, 15, 30, 60)   # seconds after the warmer starts
_warm_lock = threading.Lock()
_warmer_started = False
_warmer_thread: threading.Thread | None = None
_last_warm_kick = 0.0
WARMUP_KICK_COOLDOWN = 30.0          # min seconds between request-triggered kicks
_turso_warm = False


def turso_warm() -> bool:
    """True once a warm-up probe (SELECT 1) has succeeded on this instance."""
    return _turso_warm


def warmer_started() -> bool:
    return _warmer_thread is not None and _warmer_thread.is_alive()


def _warm_probe() -> bool:
    """One trivial round-trip. Returns True the moment Turso answers."""
    global _turso_warm
    if MODE != "turso":
        return False
    try:
        _turso_run([("SELECT 1", ())])
    except Exception as exc:  # noqa: BLE001 — warm-up is best-effort
        log.debug("turso warm-up probe failed: %s: %s", type(exc).__name__, exc)
        return False
    if not _turso_warm:
        _turso_warm = True
        log.info("turso warm")
    return True


def _warmer_loop() -> None:
    started = time.monotonic()
    for offset in WARMUP_DELAYS:
        wait = offset - (time.monotonic() - started)
        if wait > 0:
            time.sleep(wait)
        if _warm_probe():
            return
    log.warning("turso did not warm up within %ss — the app keeps working and "
                "retries per request", WARMUP_DELAYS[-1])


def start_warmer(force: bool = False) -> bool:
    """Start the background warm-up thread. Idempotent and never blocking."""
    global _warmer_started, _warmer_thread
    if not TURSO_CONFIGURED or MODE != "turso" or _turso_warm:
        return False
    with _warm_lock:
        if _warmer_thread is not None and _warmer_thread.is_alive():
            return False
        if _warmer_started and not force:
            return False
        _warmer_started = True
        _warmer_thread = threading.Thread(target=_warmer_loop, name="turso-warmer",
                                          daemon=True)
        _warmer_thread.start()
        log.info("turso warm-up started (checks at %s)",
                 ", ".join(f"{d}s" for d in WARMUP_DELAYS))
        return True


def kick_warmer() -> None:
    """Called from the request path: start warm-up if the thread is gone.

    Only ever spawns a daemon thread and returns immediately, so an in-flight
    request is never delayed by it. Rate-limited so a burst of requests cannot
    spawn a burst of threads.
    """
    global _last_warm_kick
    if _turso_warm or not TURSO_CONFIGURED or MODE != "turso":
        return
    now = time.monotonic()
    if now - _last_warm_kick < WARMUP_KICK_COOLDOWN:
        return
    _last_warm_kick = now
    start_warmer(force=True)


# --------------------------------------------------------------------------
# Automatic recovery (degraded -> Turso)
# --------------------------------------------------------------------------
# Degrading to /tmp must be a *state*, not a verdict. While the engine is
# degraded but Turso is configured, a daemon thread probes the database every
# ~30 s (and immediately when the request path wakes it) and flips the engine
# back the moment the database answers — schema initialisation included. The
# old behaviour (sticky until the next redeploy) is gone.

RECOVERY_INTERVAL = 30.0
_recovery_lock = threading.Lock()
_recovery_thread: threading.Thread | None = None
_recovery_wake = threading.Event()


def recovery_active() -> bool:
    return _recovery_thread is not None and _recovery_thread.is_alive()


def _recovery_probe() -> bool:
    """One attempt to flip back. True when the engine is on Turso again (or
    was already there). Never raises — the probe is best-effort by design."""
    global MODE, _db_warning, _turso_error, _turso_failures, _turso_ever_worked
    if MODE == "turso":
        return True
    try:
        _turso_run([("SELECT 1", ())])          # 1. reachable + auth ok
    except Exception as exc:  # noqa: BLE001 — a failed probe just waits
        log.debug("turso recovery probe failed: %s: %s",
                  type(exc).__name__, exc)
        return False
    if not ensure_schema(force=True):           # 2. schema (idempotent)
        return False
    MODE = "turso"                              # 3. flip back
    _db_warning = ""
    _turso_error = ""
    _turso_failures = 0
    _turso_ever_worked = True
    log.info("turso recovered — back on persistent storage")
    return True


def _recovery_loop() -> None:
    while MODE != "turso":
        try:
            _recovery_probe()
        except Exception:  # noqa: BLE001 — the loop must outlive probe bugs
            log.debug("turso recovery probe crashed", exc_info=True)
        _recovery_wake.wait(RECOVERY_INTERVAL)
        _recovery_wake.clear()


def start_recovery() -> bool:
    """Start (or wake) the background recovery thread. Idempotent, non-blocking,
    and a no-op unless Turso is configured and the engine is degraded."""
    global _recovery_thread
    if not TURSO_CONFIGURED or MODE == "turso":
        return False
    with _recovery_lock:
        if _recovery_thread is not None and _recovery_thread.is_alive():
            _recovery_wake.set()
            return False
        _recovery_thread = threading.Thread(target=_recovery_loop,
                                            name="turso-recovery", daemon=True)
        _recovery_thread.start()
        log.info("turso recovery started (probe every %ss)", RECOVERY_INTERVAL)
        return True


def kick_recovery() -> None:
    """Request-path hook: while degraded, make sure a recovery probe is coming.

    Called from every public DB entry point; when degraded it only takes a
    lock and checks thread liveness, so it adds no measurable latency.
    """
    if TURSO_CONFIGURED and MODE != "turso":
        start_recovery()


def status() -> dict:
    backend = "Turso (persistent)" if MODE == "turso" else (
        "Local SQLite file" if not ON_VERCEL else "Temporary storage (/tmp)")
    warning = _db_warning
    if not TURSO_CONFIGURED and ON_VERCEL:
        warning = ("No Turso configured — accounts and chats are stored in "
                   "temporary storage and can disappear when the app restarts.")
    if _schema_error:
        warning = ("Storage tables could not be created — the app keeps retrying. "
                   "Sign-in and saving may fail until this is fixed.")
    return {
        "mode": MODE,
        "label": backend,
        "persistent": is_persistent(),
        "warning": warning,
        "turso_configured": TURSO_CONFIGURED,
        "turso_error": _turso_error,
        "turso_warm": _turso_warm,
        "warmer_started": warmer_started(),
        "recovery_active": recovery_active(),
        "schema": {"ready": _schema_ready, "error": _schema_error},
    }

# --------------------------------------------------------------------------
# Local SQLite helpers
# --------------------------------------------------------------------------

def _local_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _sqlite_query(sql: str, params: Sequence[Any]) -> list[dict]:
    with _write_lock:
        conn = _local_connect()
        try:
            rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        finally:
            conn.close()
    return rows


def _sqlite_execute(sql: str, params: Sequence[Any]) -> dict:
    with _write_lock:
        conn = _local_connect()
        try:
            cur = conn.execute(sql, tuple(params))
            conn.commit()
            return {
                "affected": cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0,
                "last_insert_rowid": cur.lastrowid,
            }
        finally:
            conn.close()


# --------------------------------------------------------------------------
# Turso HTTP (Hrana v2 pipeline) helpers
# --------------------------------------------------------------------------

def _encode_arg(value: Any) -> dict:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "integer", "value": 1 if value else 0}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, (bytes, bytearray)):
        return {"type": "blob", "value": base64.b64encode(bytes(value)).decode()}
    return {"type": "text", "value": str(value)}


def _decode_cell(cell: dict) -> Any:
    ctype = cell.get("type")
    value = cell.get("value")
    if ctype == "null" or value is None:
        return None
    if ctype == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if ctype == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if ctype == "blob":
        try:
            return base64.b64decode(value)
        except Exception:
            return value
    return value


def _parse_pipeline_response(payload: dict) -> list[dict]:
    """Returns one entry per request: {'rows': [...], 'cols': [...],
    'affected': int, 'last_insert_rowid': ...} or raises on error."""
    import httpx

    results = []
    for item in payload.get("results", []):
        if item.get("type") == "error":
            err = item.get("error", {})
            raise RuntimeError(f"Turso error: {err.get('message', item)}")
        if item.get("type") != "ok":
            raise RuntimeError(f"Turso unexpected result: {item}")
        resp = item.get("response", {})
        result = resp.get("result", resp if "rows" in resp else {})
        cols = [c.get("name") for c in result.get("cols", [])]
        rows = [
            {cols[i]: _decode_cell(cell) for i, cell in enumerate(row)}
            for row in result.get("rows", [])
        ]
        rowid = result.get("last_insert_rowid")
        if isinstance(rowid, dict):
            rowid = _decode_cell(rowid)
        results.append({
            "rows": rows,
            "cols": cols,
            "affected": int(result.get("affected_row_count") or 0),
            "last_insert_rowid": rowid,
        })
    return results


def _turso_run(statements: list[tuple[str, Sequence[Any]]]) -> list[dict]:
    import httpx

    requests = [
        {
            "type": "execute",
            "stmt": {"sql": sql, "args": [_encode_arg(v) for v in params]},
        }
        for sql, params in statements
    ]
    requests.append({"type": "close"})
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            PIPELINE_URL,
            json={"requests": requests},
            headers={
                "Authorization": f"Bearer {TURSO_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return _parse_pipeline_response(resp.json())


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def _query_raw(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    if MODE == "turso":
        return _turso_guard(lambda: _turso_run([(sql, params)])[0]["rows"])
    return _sqlite_query(sql, params)


def _execute_raw(sql: str, params: Sequence[Any] = ()) -> dict:
    if MODE == "turso":
        r = _turso_guard(lambda: _turso_run([(sql, params)])[0])
        return {"affected": r["affected"], "last_insert_rowid": r["last_insert_rowid"]}
    return _sqlite_execute(sql, params)


def _execute_many_raw(statements: Iterable[tuple[str, Sequence[Any]]]) -> None:
    stmts = list(statements)
    if not stmts:
        return
    if MODE == "turso":
        _turso_guard(lambda: _turso_run(stmts))
        return
    with _write_lock:
        conn = _local_connect()
        try:
            for sql, params in stmts:
                conn.execute(sql, tuple(params))
            conn.commit()
        finally:
            conn.close()


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        meta TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_id, updated_at)",
    """
    CREATE TABLE IF NOT EXISTS usage_counters (
        day TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        count INTEGER DEFAULT 0,
        PRIMARY KEY (day, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_memories_user ON memories (user_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS personas (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        prompt TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shared_chats (
        token TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS docs (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decks (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        subtitle TEXT DEFAULT '',
        topic TEXT DEFAULT '',
        outline TEXT NOT NULL,
        status TEXT DEFAULT 'planning',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_decks_user ON decks (user_id, updated_at)",
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        conversation_id TEXT NOT NULL,
        prompt TEXT NOT NULL,
        hour_utc INTEGER DEFAULT 6,
        last_run TEXT,
        active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]


# Column migrations as (table, column, statement). The list is the single
# source of truth for both the PRAGMA pre-check and the ALTER itself, so the
# two can never drift apart.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("users", "custom_instructions",
     "ALTER TABLE users ADD COLUMN custom_instructions TEXT DEFAULT ''"),
    ("users", "auto_memory",
     "ALTER TABLE users ADD COLUMN auto_memory INTEGER DEFAULT 1"),
    ("conversations", "folder",
     "ALTER TABLE conversations ADD COLUMN folder TEXT DEFAULT ''"),
    ("conversations", "pinned",
     "ALTER TABLE conversations ADD COLUMN pinned INTEGER DEFAULT 0"),
    ("users", "prefs",
     "ALTER TABLE users ADD COLUMN prefs TEXT DEFAULT '{}'"),
    ("decks", "audience",
     "ALTER TABLE decks ADD COLUMN audience TEXT DEFAULT ''"),
    ("decks", "tone",
     "ALTER TABLE decks ADD COLUMN tone TEXT DEFAULT ''"),
]


def _column_exists(table: str, column: str) -> bool | None:
    """PRAGMA table_info lookup: True/False when answerable, None when the
    introspection itself failed (then the ALTER attempt decides the outcome)."""
    try:
        rows = _query_raw(f"PRAGMA table_info({table})", ())
    except Exception:
        return None
    return any(str(row.get("name", "")).lower() == column.lower() for row in rows)


def _add_column(table: str, column: str, sql: str) -> None:
    """Idempotent ALTER TABLE ADD COLUMN.

    1. Pre-check with PRAGMA table_info: the column is already there -> no-op.
       On a database that a previous deploy migrated this is the normal path,
       and it costs cheap introspection round-trips instead of seven errors.
    2. Otherwise run the ALTER. A 'duplicate column name' error means another
       init (a concurrent thread or another serverless instance) added the
       column between the check and the ALTER — that is success, not a
       failure, and it never counts as a Turso outage.
    3. Any other error is re-raised: it is a real database problem.
    """
    if _column_exists(table, column) is True:
        return
    try:
        _execute_raw(sql, ())
    except Exception as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise


def _run_migrations() -> None:
    """Idempotent column additions — safe to re-run after a schema rebuild."""
    for table, column, sql in MIGRATIONS:
        _add_column(table, column, sql)


def init_db() -> bool:
    """Create the schema (idempotent). Returns True when the database is usable.

    Never raises: a broken database must degrade into an honest banner, not a
    dead app. The first statement of the next request retries automatically.
    """
    return ensure_schema(force=True)


# --------------------------------------------------------------------------
# Self-healing schema
# --------------------------------------------------------------------------
# init_db() runs once at import. If the database is later replaced — a wiped
# /tmp on a serverless instance, a fresh Turso database, an operator swapping
# DATABASE_URL — every request would otherwise fail with "no such table" and
# the app would 500 forever. Instead we detect that class of error, rebuild
# the schema, and retry the statement once.
#
# _schema_lock is the single serialiser for schema work: the import-time
# init, the warmer's first probe, and every concurrent first request all
# funnel through ensure_schema(), so migrations run at most once per
# database state even when all of them arrive at the same instant.

_SCHEMA_MARKERS = ("no such table", "no such column", "does not exist",
                   "unknown column", "no such index")

_schema_lock = threading.RLock()


def _schema_missing(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _SCHEMA_MARKERS)


def ensure_schema(force: bool = False) -> bool:
    """Create tables/indexes/migrations if needed. Returns True when usable."""
    global _schema_ready, _schema_building, _schema_error
    with _schema_lock:
        if _schema_ready and not force:
            return True
        if _schema_building:            # re-entrant call from init_db() itself
            return False
        _schema_building = True
        try:
            _execute_many_raw([(statement, ()) for statement in SCHEMA])
            _run_migrations()
            _schema_ready = True
            _schema_error = ""
            return True
        except Exception as exc:  # noqa: BLE001 — surfaced through status()
            _schema_error = f"{type(exc).__name__}: {exc}"[:300]
            _schema_ready = False
            return False
        finally:
            _schema_building = False


def _with_schema_retry(call):
    """Run a statement; if the schema vanished underneath us, rebuild and retry."""
    try:
        return call()
    except Exception as exc:  # noqa: BLE001
        if not _schema_missing(exc):
            raise
        if not ensure_schema(force=True):
            raise
        return call()


def query(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    if not _schema_ready:
        ensure_schema()
    kick_recovery()
    return _with_schema_retry(lambda: _query_raw(sql, params))


def execute(sql: str, params: Sequence[Any] = ()) -> dict:
    if not _schema_ready:
        ensure_schema()
    kick_recovery()
    return _with_schema_retry(lambda: _execute_raw(sql, params))


def execute_many(statements: Iterable[tuple[str, Sequence[Any]]]) -> None:
    stmts = list(statements)
    if not stmts:
        return
    if not _schema_ready:
        ensure_schema()
    kick_recovery()
    _with_schema_retry(lambda: _execute_many_raw(stmts))


def schema_health() -> dict:
    """Exposed through /api/ai/status -> storage.schema."""
    return {"ready": _schema_ready, "error": _schema_error}
