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

if TURSO_URL:
    # Accept libsql:// URLs too and convert to HTTPS for the HTTP API.
    if TURSO_URL.startswith("libsql://"):
        TURSO_URL = "https://" + TURSO_URL[len("libsql://"):]
    PIPELINE_URL = TURSO_URL + "/v2/pipeline"
    MODE = "turso"
else:
    PIPELINE_URL = ""
    MODE = "sqlite"

SQLITE_PATH = (
    os.getenv("DATABASE_URL", "/tmp/aether.db" if os.getenv("VERCEL") else "aether.db")
    .replace("sqlite:///", "")
    .replace("sqlite://", "")
)

_write_lock = threading.Lock()

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
        return _turso_run([(sql, params)])[0]["rows"]
    return _sqlite_query(sql, params)


def execute(sql: str, params: Sequence[Any] = ()) -> dict:
    if MODE == "turso":
        r = _turso_run([(sql, params)])[0]
        return {"affected": r["affected"], "last_insert_rowid": r["last_insert_rowid"]}
    return _sqlite_execute(sql, params)


def execute_many(statements: Iterable[tuple[str, Sequence[Any]]]) -> None:
    stmts = list(statements)
    if not stmts:
        return
    if MODE == "turso":
        _turso_run(stmts)
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
]


def init_db() -> None:
    execute_many([(s, ()) for s in SCHEMA])
