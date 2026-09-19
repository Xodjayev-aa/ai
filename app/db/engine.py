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
import os
import sqlite3
import threading
from typing import Any, Iterable, Sequence

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

if TURSO_CONFIGURED:
    PIPELINE_URL = TURSO_URL + "/v2/pipeline"
    MODE = "turso"
else:
    PIPELINE_URL = ""
    MODE = "sqlite"
    if TURSO_URL and not TURSO_TOKEN:
        _partial_turso = ("TURSO_DATABASE_URL is set but TURSO_AUTH_TOKEN is "
                          "missing — using temporary storage.")
    else:
        _partial_turso = ""

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


def _degrade_to_sqlite(exc: Exception) -> None:
    global MODE, _db_warning, _turso_error
    _turso_error = f"{type(exc).__name__}: {exc}"[:300]
    if MODE == "turso":
        MODE = "sqlite"
        _db_warning = (
            "Turso is configured but unreachable — falling back to temporary "
            "storage. Data may not persist until TURSO_DATABASE_URL / "
            "TURSO_AUTH_TOKEN are fixed."
        )


def _turso_guard(call):
    """Run a Turso call; degrade to SQLite when Turso never works or keeps failing."""
    global _turso_failures, _turso_ever_worked
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001 — any failure counts
        _turso_failures += 1
        if not _turso_ever_worked or _turso_failures >= 3:
            _degrade_to_sqlite(exc)
        raise
    _turso_failures = 0
    _turso_ever_worked = True
    return result


def is_persistent() -> bool:
    """True when data survives a cold start of the serverless instance."""
    return MODE == "turso" or not ON_VERCEL


def status() -> dict:
    backend = "Turso (persistent)" if MODE == "turso" else (
        "Local SQLite file" if not ON_VERCEL else "Temporary storage (/tmp)")
    warning = _db_warning
    if not TURSO_CONFIGURED and ON_VERCEL:
        warning = ("No Turso configured — accounts and chats are stored in "
                   "temporary storage and can disappear when the app restarts.")
    return {
        "mode": MODE,
        "label": backend,
        "persistent": is_persistent(),
        "warning": warning,
        "turso_configured": TURSO_CONFIGURED,
        "turso_error": _turso_error,
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

def query(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    if MODE == "turso":
        return _turso_guard(lambda: _turso_run([(sql, params)])[0]["rows"])
    return _sqlite_query(sql, params)


def execute(sql: str, params: Sequence[Any] = ()) -> dict:
    if MODE == "turso":
        r = _turso_guard(lambda: _turso_run([(sql, params)])[0])
        return {"affected": r["affected"], "last_insert_rowid": r["last_insert_rowid"]}
    return _sqlite_execute(sql, params)


def execute_many(statements: Iterable[tuple[str, Sequence[Any]]]) -> None:
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


def _safe_alter(sql: str) -> None:
    """Add a column, ignoring 'duplicate column' errors. Works on both
    SQLite and Turso (PRAGMA introspection is unreliable over HTTP)."""
    try:
        execute(sql, ())
    except Exception:
        pass


def init_db() -> None:
    execute_many([(s, ()) for s in SCHEMA])
    # -- lightweight migrations (idempotent) --
    _safe_alter("ALTER TABLE users ADD COLUMN custom_instructions TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE users ADD COLUMN auto_memory INTEGER DEFAULT 1")
    _safe_alter("ALTER TABLE conversations ADD COLUMN folder TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE conversations ADD COLUMN pinned INTEGER DEFAULT 0")
