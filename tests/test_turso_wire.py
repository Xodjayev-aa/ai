"""Wire-level test for the Turso "SQL over HTTP" (Hrana v2) encoding.

Regression for the production 400 Bad Request on registration: Turso's
/v2/pipeline endpoint rejects argument values that are not JSON strings
(``{"type": "integer", "value": 0}`` -> HTTP 400 "invalid argument"),
and blobs must use the ``base64`` key, not ``value``. The old encoder
sent bare numbers and blob-in-``value``, so the very first parameterized
INSERT (the signup row) blew up with a 400 and registration failed.

This test stands up a real local HTTP server that enforces the spec —
400 for any non-conforming payload, 200 for a correct one — and pushes a
parameterized INSERT through the real ``_turso_run`` against it:

    1. the exact wire args are [{"type": "text", ...}, {"type": "text", ...},
       {"type": "integer", "value": "0"}] — numbers as JSON strings
    2. the round trip parses back: close-response skipped, string
       last_insert_rowid coerced to int
    3. a genuine 400 surfaces as RuntimeError("Turso HTTP 400: <body>")
    4. such 401/403/404s classify as misconfiguration, a 500 does not

    .venv/bin/python -m pytest tests/test_turso_wire.py -q
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

os.environ.setdefault("APP_SECRET", "turso-wire-test-secret")
os.environ.setdefault("DATABASE_URL",
                      os.path.join(tempfile.gettempdir(), "aether-wire-test.db"))
os.environ.pop("VERCEL", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import engine  # noqa: E402


# --------------------------------------------------------------------------
# A spec-enforcing Turso stand-in
# --------------------------------------------------------------------------

def _validate_arg(arg) -> str:
    """Return an error string if `arg` violates the SQL-over-HTTP spec."""
    if not isinstance(arg, dict):
        return f"arg is not an object: {arg!r}"
    atype = arg.get("type")
    if atype in ("integer", "float", "text"):
        value = arg.get("value")
        if not isinstance(value, str):
            return (f"{atype} arg value must be a JSON string, got "
                    f"{type(value).__name__}: {value!r}")
        if "base64" in arg:
            return f"{atype} arg must not carry a base64 key"
    elif atype == "blob":
        if "value" in arg:
            return "blob arg must use the 'base64' key, not 'value'"
        if not isinstance(arg.get("base64"), str):
            return f"blob base64 must be a JSON string: {arg.get('base64')!r}"
    elif atype == "null":
        if arg.get("value") is not None:
            return f"null arg value must be null: {arg.get('value')!r}"
    else:
        return f"unknown arg type: {atype!r}"
    return ""


class SpecHandler(BaseHTTPRequestHandler):
    """POST /v2/pipeline: 400 on any spec violation, 200 with a realistic
    execute + close response otherwise. Records every accepted body."""

    server_version = "SpecTurso/1"

    def log_message(self, *args):  # silence request logging
        pass

    def _reply(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception:
            self._reply(400, {"error": "invalid JSON"})
            return

        if getattr(self.server, "force_status", 0):
            self._reply(self.server.force_status, {"error": "forced failure"})
            return

        requests = payload.get("requests")
        if not isinstance(requests, list) or not requests:
            self._reply(400, {"error": "pipeline requires a non-empty 'requests'"})
            return

        results = []
        for req in requests:
            rtype = req.get("type")
            if rtype == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            if rtype != "execute":
                self._reply(400, {"error": f"unknown request type {rtype!r}"})
                return
            stmt = req.get("stmt") or {}
            for arg in stmt.get("args") or []:
                problem = _validate_arg(arg)
                if problem:
                    self._reply(400, {"error": f"invalid argument: {problem}"})
                    return
            results.append({
                "type": "ok",
                "response": {
                    "type": "execute",
                    "result": {
                        "cols": [],
                        "rows": [],
                        "affected_row_count": 1,
                        # Real Turso returns this as a JSON string; the
                        # engine must hand back an int.
                        "last_insert_rowid": "7",
                    },
                },
            })

        self.server.received.append(payload)
        self._reply(200, {"baton": None, "base_url": None, "results": results})


@pytest.fixture()
def spec_server(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), SpecHandler)
    server.received = []
    server.force_status = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(engine, "PIPELINE_URL",
                        f"http://127.0.0.1:{server.server_address[1]}/v2/pipeline")
    monkeypatch.setattr(engine, "TURSO_TOKEN", "wire-test-token")
    yield server
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


# --------------------------------------------------------------------------
# The round trip
# --------------------------------------------------------------------------

def test_insert_roundtrip_sends_spec_wire_args(spec_server):
    """2 text args + 1 int through the REAL _turso_run: the server 400s any
    non-spec encoding, so success proves the wire format is exactly right."""
    results = engine._turso_run([
        ("INSERT INTO users (username, email, failed_attempts) "
         "VALUES (?, ?, ?)",
         ("alice", "alice@example.com", 0)),
    ])

    assert len(results) == 1, "the close-response yields no result entry"
    entry = results[0]
    assert entry["affected"] == 1
    assert entry["last_insert_rowid"] == 7  # string "7" on the wire -> int
    assert isinstance(entry["last_insert_rowid"], int)

    (sent,) = spec_server.received
    execute, close = sent["requests"]
    assert close == {"type": "close"}
    assert execute["stmt"]["args"] == [
        {"type": "text", "value": "alice"},
        {"type": "text", "value": "alice@example.com"},
        {"type": "integer", "value": "0"},
    ], "numbers must travel as JSON strings (SQL-over-HTTP spec)"


def test_bool_float_null_blob_encodings_match_spec(spec_server):
    """Every remaining scalar type, in one parameterized statement."""
    engine._turso_run([
        ("UPDATE t SET a=?, b=?, c=?, d=?", (True, 2.5, None, b"\x00\xff")),
    ])
    (sent,) = spec_server.received
    assert sent["requests"][0]["stmt"]["args"] == [
        {"type": "integer", "value": "1"},
        {"type": "float", "value": "2.5"},
        {"type": "null", "value": None},
        {"type": "blob", "base64": "AP8="},
    ]


def test_the_server_itself_rejects_the_old_encoding(spec_server):
    """Sanity: the stand-in really 400s the pre-fix wire format (bare JSON
    number), so the green round trip above is meaningful."""
    import httpx

    url = engine.PIPELINE_URL
    bad = {"requests": [{
        "type": "execute",
        "stmt": {"sql": "INSERT INTO t (n) VALUES (?)",
                 "args": [{"type": "integer", "value": 0}]},
    }]}
    resp = httpx.post(url, json=bad,
                      headers={"Authorization": "Bearer wire-test-token"})
    assert resp.status_code == 400
    assert "invalid argument" in resp.text


def test_http_error_becomes_runtime_error_with_body(spec_server, monkeypatch):
    spec_server.force_status = 400
    with pytest.raises(RuntimeError) as excinfo:
        engine._turso_run([("SELECT 1", ())])
    message = str(excinfo.value)
    assert message.startswith("Turso HTTP 400")
    assert "forced failure" in message  # body detail survives


def test_4xx_classified_as_misconfiguration_500_is_not():
    for code in (401, 403, 404):
        exc = RuntimeError(f"Turso HTTP {code}: unauthorized")
        assert engine._looks_like_misconfiguration(exc), code
    assert not engine._looks_like_misconfiguration(
        RuntimeError("Turso HTTP 500: no body"))
