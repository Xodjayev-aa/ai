"""Unit tests for the idempotent Turso schema migration + auto-recovery.

Regression for the production incident behind this hotfix: on a database that
a previous deploy had already migrated, re-running the schema produced seven
"SQLite error: duplicate column name: ..." errors; the old code swallowed them
inside the migration but each one still counted as a *Turso outage vote* in
the guard, so after three the engine "degraded" to /tmp with a banner telling
the user to fix TURSO_DATABASE_URL / TURSO_AUTH_TOKEN — even though Turso was
perfectly reachable. The degrade was sticky until the next redeploy.

These tests simulate a Turso-shaped database (a real SQLite file behind the
Hrana pipeline shape, raising the exact error strings real Turso returns) and
assert:

    1. init twice on an already-migrated DB -> no error, no degrade, no ALTER
    2. the PRAGMA-vs-ALTER race -> duplicate-column is success
    3. concurrent init (4 threads) -> exactly one migration run
    4. degraded engine + Turso heals -> automatic flip back (schema included)
    5. a real 401 -> immediate degrade with the env-var hint (misconfig only)
    6. recovery never starts when Turso is not configured

    .venv/bin/python -m pytest tests/test_turso_migration.py -q
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import time

import pytest

os.environ.setdefault("APP_SECRET", "turso-test-secret")
os.environ.setdefault("DATABASE_URL",
                      os.path.join(tempfile.gettempdir(), "aether-turso-test.db"))
os.environ.pop("VERCEL", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import engine  # noqa: E402


# --------------------------------------------------------------------------
# A Turso-shaped fake
# --------------------------------------------------------------------------

class FakeTurso:
    """One SQLite file behind the Hrana v2 pipeline shape.

    Executes the exact SQL the engine sends (CREATE TABLE IF NOT EXISTS,
    PRAGMA table_info, ALTER TABLE ADD COLUMN, SELECT 1) and wraps errors the
    way real Turso does: ``RuntimeError("Turso error: SQLite error: ...")``.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.alter_attempts = 0
        self.alter_successes = 0
        self.down = False            # simulate an outage (connection errors)
        self.pragma_stale = False    # simulate the check/race window
        self._connect = lambda: sqlite3.connect(self.path, check_same_thread=False)

    def set_db(self, path: str) -> None:
        self.path = path
        self._connect = lambda: sqlite3.connect(path, check_same_thread=False)

    def run(self, statements: list[tuple[str, tuple]]) -> list[dict]:
        if self.down:
            raise ConnectionError("simulated Turso outage")
        results: list[dict] = []
        with self.lock:
            conn = self._connect()
            try:
                for sql, params in statements:
                    s = sql.strip()
                    upper = s.upper()
                    if upper.startswith("PRAGMA TABLE_INFO"):
                        cur = conn.execute(s)
                        cols = [d[0] for d in cur.description or []]
                        rows = [{cols[i]: r[i] for i in range(len(cols))}
                                for r in cur.fetchall()]
                        if self.pragma_stale:
                            rows = []  # stale view: "the column was never added"
                        results.append({"rows": rows, "cols": cols, "affected": 0,
                                        "last_insert_rowid": None})
                    elif upper.startswith("ALTER TABLE"):
                        self.alter_attempts += 1
                        try:
                            cur = conn.execute(s)
                            conn.commit()
                            self.alter_successes += 1
                            results.append({"rows": [], "cols": [],
                                            "affected": cur.rowcount,
                                            "last_insert_rowid": None})
                        except sqlite3.OperationalError as err:
                            # Exactly what real Turso returns over HTTP:
                            raise RuntimeError(f"Turso error: SQLite error: {err}")
                    else:
                        cur = conn.execute(s, tuple(params))
                        conn.commit()
                        if cur.description:
                            cols = [d[0] for d in cur.description]
                            rows = [{cols[i]: r[i] for i in range(len(cols))}
                                    for r in cur.fetchall()]
                        else:
                            cols, rows = [], []
                        results.append({"rows": rows, "cols": cols,
                                        "affected": cur.rowcount or 0,
                                        "last_insert_rowid": cur.lastrowid})
                return results
            finally:
                conn.close()


MIGRATED_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    custom_instructions TEXT DEFAULT '', auto_memory INTEGER DEFAULT 1,
    prefs TEXT DEFAULT '{}');
CREATE TABLE conversations (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
    title TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    folder TEXT DEFAULT '', pinned INTEGER DEFAULT 0);
CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
    user_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
    meta TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE usage_counters (day TEXT NOT NULL, user_id INTEGER NOT NULL,
    count INTEGER DEFAULT 0, PRIMARY KEY (day, user_id));
CREATE TABLE memories (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
    content TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE personas (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
    name TEXT NOT NULL, prompt TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE shared_chats (token TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE docs (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
    name TEXT NOT NULL, text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE decks (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
    title TEXT NOT NULL, subtitle TEXT DEFAULT '', topic TEXT DEFAULT '',
    outline TEXT NOT NULL, status TEXT DEFAULT 'planning',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    audience TEXT DEFAULT '', tone TEXT DEFAULT '');
CREATE TABLE tasks (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL, prompt TEXT NOT NULL,
    hour_utc INTEGER DEFAULT 6, last_run TEXT, active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
"""


def _stop_recovery() -> None:
    """Wait for any recovery thread from a previous test to leave."""
    engine._recovery_wake.set()
    thread = engine._recovery_thread
    if thread is not None and thread.is_alive() \
            and thread is not threading.current_thread():
        thread.join(timeout=2)
    engine._recovery_thread = None


@pytest.fixture()
def fake(monkeypatch):
    """Wire the engine to a Turso-shaped fake and reset its health state."""
    dbfile = os.path.join(tempfile.mkdtemp(prefix="turso-fake-"), "fake.db")
    ft = FakeTurso(dbfile)
    monkeypatch.setattr(engine, "_turso_run", ft.run)
    monkeypatch.setattr(engine, "TURSO_CONFIGURED", True)
    monkeypatch.setattr(engine, "PIPELINE_URL", "https://fake.turso.io/v2/pipeline")
    monkeypatch.setattr(engine, "MODE", "turso")
    monkeypatch.setattr(engine, "_turso_failures", 0)
    monkeypatch.setattr(engine, "_turso_ever_worked", False)
    monkeypatch.setattr(engine, "_turso_error", "")
    monkeypatch.setattr(engine, "_db_warning", "")
    monkeypatch.setattr(engine, "_schema_ready", False)
    monkeypatch.setattr(engine, "_schema_building", False)
    monkeypatch.setattr(engine, "_schema_error", "")
    monkeypatch.setattr(engine, "RECOVERY_INTERVAL", 0.2)
    _stop_recovery()
    yield ft
    ft.down = False  # keep the recovery probe from spinning during teardown
    _stop_recovery()


def _pre_migrate(ft: FakeTurso) -> None:
    """Create the schema exactly as a previous deploy left it: every
    migration column already present (the production state that broke)."""
    conn = sqlite3.connect(ft.path)
    conn.executescript(MIGRATED_SCHEMA)
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# 1. The production incident, end to end
# --------------------------------------------------------------------------

def test_init_twice_on_premigrated_db_no_error_no_degrade(fake):
    _pre_migrate(fake)
    assert engine.init_db() is True, "re-init on a migrated DB must succeed"
    assert engine.init_db() is True, "a second init must succeed too"
    assert engine.MODE == "turso", "a migrated schema must never read as an outage"
    assert engine._db_warning == "", "no banner may be raised"
    assert engine._turso_error == "", "no turso_error may be recorded"
    assert engine._turso_failures == 0, "duplicate-column must not count as a failure"
    assert fake.alter_attempts == 0, "the PRAGMA pre-check must skip every ALTER"
    status = engine.status()
    assert status["mode"] == "turso" and status["persistent"] is True
    assert "unreachable" not in status["warning"]


def test_duplicate_column_race_is_success_not_outage(fake):
    _pre_migrate(fake)
    # The race window: the PRAGMA check returns a stale view (empty) and the
    # ALTER then hits the column a concurrent init just added.
    fake.pragma_stale = True
    assert engine.init_db() is True
    assert fake.alter_attempts == 7, "every ALTER is attempted"
    assert fake.alter_successes == 0, "none of them actually ran"
    assert engine.MODE == "turso", "seven duplicate-column errors are still success"
    assert engine._db_warning == "" and engine._turso_error == ""
    assert engine._turso_failures == 0


# --------------------------------------------------------------------------
# 2. Serialisation: concurrent init runs the migrations exactly once
# --------------------------------------------------------------------------

def test_concurrent_init_exactly_one_migration_run(fake):
    # Fresh (unmigrated) database, several threads racing for first boot.
    barrier = threading.Barrier(4)
    results: list = []
    errors: list = []

    def worker() -> None:
        barrier.wait()
        try:
            results.append(engine.init_db())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent init raised: {errors}"
    assert results == [True] * 4
    assert engine.MODE == "turso"
    assert fake.alter_attempts == 7, "exactly one init ran the migrations"
    assert engine._schema_ready is True


# --------------------------------------------------------------------------
# 3. Auto-recovery: degraded -> Turso heals -> flip back (schema included)
# --------------------------------------------------------------------------

def test_degraded_engine_reprobe_flips_back_on_heal(fake):
    _pre_migrate(fake)
    # Simulate a cold/dead Turso: the first real queries fail with a
    # connection error until three have accumulated.
    fake.down = True
    for _ in range(6):
        if engine.MODE == "sqlite":
            break
        try:
            engine.execute("SELECT 1", ())
        except Exception:
            pass
    assert engine.MODE == "sqlite", "three consecutive failures degrade"
    assert "hiccup" in engine._db_warning.lower(), \
        f"banner must be honest, got: {engine._db_warning!r}"
    assert "unreachable" not in engine._db_warning, \
        "a migration/connection hiccup must not be labelled 'unreachable'"
    assert "TURSO_DATABASE_URL" not in engine._db_warning, \
        "only a real misconfiguration may blame the env vars"
    assert engine.recovery_active(), "degrade must start the background probe"

    # Turso heals: the next probe (woken by the request path) flips back.
    fake.down = False
    engine.kick_recovery()
    deadline = time.time() + 10
    while time.time() < deadline and engine.MODE != "turso":
        time.sleep(0.05)
    assert engine.MODE == "turso", "the engine must flip back automatically"
    assert engine._db_warning == "", "the banner clears on recovery"
    assert engine._turso_error == "", "the old error is cleared on recovery"
    status = engine.status()
    assert status["mode"] == "turso"
    assert status["persistent"] is True
    assert status["warning"] == ""
    assert status["schema"]["ready"] is True, "schema init is part of recovery"


def test_recovery_never_starts_without_turso(fake, monkeypatch):
    monkeypatch.setattr(engine, "TURSO_CONFIGURED", False)
    monkeypatch.setattr(engine, "MODE", "sqlite")
    assert engine.start_recovery() is False
    assert not engine.recovery_active()
    assert engine.kick_recovery() is None  # no-op, no thread


# --------------------------------------------------------------------------
# 4. Misconfiguration is the only case that may blame the env vars
# --------------------------------------------------------------------------

def test_bad_token_degrades_immediately_with_env_hint(fake, monkeypatch):
    _pre_migrate(fake)
    import httpx

    request = httpx.Request("POST", "https://fake.turso.io/v2/pipeline")
    response = httpx.Response(401, request=request)

    def auth_error(statements):
        raise httpx.HTTPStatusError("Client error '401 Unauthorized'",
                                    request=request, response=response)

    monkeypatch.setattr(engine, "_turso_run", auth_error)
    try:
        engine.execute("SELECT 1", ())  # may raise or fall through to /tmp
    except Exception:
        pass
    assert engine.MODE == "sqlite", "a 401 degrades immediately (never 'worked')"
    assert "misconfigured" in engine._db_warning.lower()
    assert "TURSO_DATABASE_URL" in engine._db_warning and "TURSO_AUTH_TOKEN" in engine._db_warning
    assert engine._turso_error, "the real 401 is reported as the turso_error"


def test_duplicate_column_never_classified_as_unreachable(fake):
    """The literal production error string must not trigger a degrade, even
    after three in a row (the old threshold), even with _turso_ever_worked
    still False (fresh instance)."""
    _pre_migrate(fake)
    fake.pragma_stale = True  # force all seven duplicate-column errors
    engine._turso_ever_worked = False
    assert engine.init_db() is True
    assert engine.MODE == "turso"
    assert "unreachable" not in engine._db_warning
    assert engine._turso_error == ""
