"""Offline behavioural tests: the whole backend with a stubbed keyless AI.

These run anywhere (no network, no keys): the free-tier provider is replaced
with a deterministic fake, so we can assert on streaming semantics, stop-safety,
regenerate, teach/memory, usage metering and both presentation exports.

    python -m pytest tests/test_offline.py -q      (or plain: python tests/test_offline.py)
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile

os.environ.setdefault("DATABASE_URL", os.path.join(tempfile.gettempdir(), "aether-test.db"))
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ.pop("VERCEL", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.ai import keyless  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}"
          f"{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(name)


# ----------------------------------------------------------- fake provider

async def fake_stream_text(messages, **kwargs):
    """Deterministic two-token stream, like a very fast free model."""
    for token in ("Hello", " there", " from", " Aether."):
        yield token


async def fake_stream_text_slow(messages, **kwargs):
    import asyncio

    await asyncio.sleep(0.4)
    yield "Slow"
    await asyncio.sleep(0.4)
    yield " answer"


async def fake_complete_text(messages, **kwargs):
    system = " ".join(m.get("content", "") for m in messages
                      if m.get("role") == "system")
    user = " ".join(str(m.get("content", "")) for m in messages
                    if m.get("role") == "user")
    if "ONE slide" in system:
        index = 0
        for word in user.split():
            if word.isdigit():
                index = int(word) - 1
                break
        return json.dumps({
            "title": f"Point {index + 1}",
            "bullets": [f"Bullet {index + 1}.1", f"Bullet {index + 1}.2",
                        f"Bullet {index + 1}.3"],
            "notes": f"Speaker note {index + 1}",
        })
    if '"slides":' in system or "STRUCTURE of a slide deck" in system:
        return json.dumps({
            "title": "Bees and Farms",
            "subtitle": "Why pollinators matter",
            "slides": [{"title": f"Point {i + 1}"} for i in range(6)],
        })
    if "memory sentence" in system:
        return "\n".join(f"- Refined memory {i + 1}"
                         for i, _ in enumerate(user.splitlines()) if _)
    if "durable facts" in system:
        return '["The user is testing Aether"]'
    return "Stub completion."


async def fake_tts_audio(text, voice="nova", **kwargs):
    return b"ID3\x03\x00\x00\x00" + b"\x00" * 4096


keyless.stream_text = fake_stream_text
keyless.complete_text = fake_complete_text
keyless.tts_audio = fake_tts_audio


def sse_events(response) -> list[dict]:
    events = []
    for frame in response.text.split("\n\n"):
        payload = None
        for line in frame.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


def main() -> int:
    # ---------------------------------------------------------------- auth
    import secrets

    email = f"tester-{secrets.token_hex(4)}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "s3cret-pass"})
    check("register", r.status_code in (200, 201), str(r.status_code))
    token = r.json().get("access_token")
    H = {"Authorization": f"Bearer {token}"}
    client.headers.update(H)

    me = client.get("/api/auth/me").json()
    check("auth/me", me.get("email") == email)

    conv = client.post("/api/conversations", json={"title": "Test"}).json()
    cid = conv["id"]

    # ------------------------------------------------------- chat streaming
    assistant_id = "11111111-2222-3333-4444-555555555555"
    r = client.post("/api/chat/stream", json={
        "conversation_id": cid, "message": "Say hello",
        "assistant_message_id": assistant_id, "client_saves_partial": True,
    })
    events = sse_events(r)
    types = [e["type"] for e in events]
    check("chat streams delta/final/done", "delta" in types and "final" in types
          and "done" in types, str(sorted(set(types))))
    final = next((e for e in events if e["type"] == "final"), {})
    check("chat answer text", final.get("text", "").strip() == "Hello there from Aether.",
          repr(final.get("text")))
    done = next((e for e in events if e["type"] == "done"), {})
    check("chat done carries the client message id",
          done.get("message_id") == assistant_id, str(done.get("message_id")))

    msgs = client.get(f"/api/conversations/{cid}/messages").json()
    check("user + assistant persisted", [m["role"] for m in msgs] == ["user", "assistant"],
          str([m["role"] for m in msgs]))

    # --------------------------------------------------------- stop / partial
    r = client.post("/api/chat/partial", json={
        "conversation_id": cid, "message_id": assistant_id,
        "content": "Hello there",  # shorter than the finished answer
    })
    check("partial save accepted", r.status_code == 200, str(r.status_code))
    msgs = client.get(f"/api/conversations/{cid}/messages").json()
    check("shorter partial never shrinks the answer",
          msgs[-1]["content"].strip() == "Hello there from Aether.",
          msgs[-1]["content"])
    check("no duplicate assistant rows", len(msgs) == 2, str(len(msgs)))

    r = client.post("/api/chat/partial", json={
        "conversation_id": cid, "message_id": "99999999-8888-7777-6666-555555555555",
        "content": "Stopped answer, kept.",
    })
    msgs = client.get(f"/api/conversations/{cid}/messages").json()
    check("stopped answer is real and kept", msgs[-1]["content"] == "Stopped answer, kept."
          and len(msgs) == 3, str(len(msgs)))

    # ----------------------------------------------------------- regenerate
    r = client.post("/api/chat/stream", json={
        "conversation_id": cid, "regenerate": True, "message": "",
        "assistant_message_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    })
    events = sse_events(r)
    check("regenerate answers", any(e["type"] == "final" for e in events))
    msgs = client.get(f"/api/conversations/{cid}/messages").json()
    users = [m for m in msgs if m["role"] == "user"]
    check("regenerate does not duplicate the question", len(users) == 1, str(len(users)))

    # -------------------------------------------------------------- truncate
    last_user = users[-1]["id"]
    r = client.post(f"/api/conversations/{cid}/truncate",
                    json={"message_id": last_user})
    check("truncate removes the tail", r.json().get("removed", 0) >= 1, str(r.json()))
    msgs = client.get(f"/api/conversations/{cid}/messages").json()
    check("conversation rewound to before the message", len(msgs) == 0, str(len(msgs)))

    # ------------------------------------------------------- teach / memory
    start = client.post("/api/teach/start").json()
    check("teach interview starts", len(start.get("questions", [])) == 5)
    client.post("/api/teach/answer", json={
        "session_id": start["session_id"], "question_id": "name", "answer": "Sam"})
    client.post("/api/teach/answer", json={
        "session_id": start["session_id"], "question_id": "style",
        "answer": "short and direct"})
    mem = client.get("/api/settings/memory").json()
    contents = [m["content"] for m in mem["memories"]]
    check("teach answers become memories",
          any("Sam" in c for c in contents) and any("short and direct" in c for c in contents),
          str(contents)[:120])
    fin = client.post("/api/teach/finish", json={"session_id": start["session_id"]}).json()
    check("teach finish refines", fin.get("answered") == 2, json.dumps(fin)[:120])

    imp = client.post("/api/teach/improve",
                      json={"instruction": "always show the steps",
                            "question": "how do I solve this"}).json()
    check("teach improves a single answer", "steps" in imp.get("memory", ""), imp.get("memory", ""))

    mem = client.get("/api/settings/memory").json()
    mid = mem["memories"][0]["id"]
    upd = client.patch(f"/api/settings/memory/{mid}", json={"content": "Edited memory text"})
    check("memory edit", upd.status_code == 200, str(upd.status_code))
    body = client.get("/api/settings/memory").json()
    check("memory edit persisted",
          any(m["content"] == "Edited memory text" for m in body["memories"]))
    dele = client.delete(f"/api/settings/memory/{mid}")
    check("memory delete", dele.status_code == 200)

    # -------------------------------------------------------------- usage
    u = client.get("/api/usage").json()
    check("usage meter counts requests", u["used_today"] > 0,
          f"used={u['used_today']} limit={u['limit']}")
    check("usage reports reset time and cooldown",
          u["resets_at"].endswith("Z") and "cooldown" in u and "queue" in u)
    check("usage is honest about shared capacity",
          "short waits" in u["capacity"]["note"].lower(), u["capacity"]["note"])

    # ------------------------------------------------------ presentations
    plan = client.post("/api/presentations/plan",
                       json={"topic": "How bees help farms", "num_slides": 6})
    check("presentation plan", plan.status_code == 200, str(plan.status_code))
    plan_data = plan.json()
    deck_id = plan_data["deck_id"]
    check("plan returns slide titles", len(plan_data["slides"]) == 6)

    slides = []
    for i in range(6):
        sr = client.post("/api/presentations/slide",
                         json={"deck_id": deck_id, "index": i})
        if sr.status_code != 200:
            check(f"slide {i}", False, f"{sr.status_code} {sr.text[:120]}")
            break
        slides.append(sr.json())
    check("all slides generated incrementally", len(slides) == 6, str(len(slides)))
    check("slide progress reported", slides[-1].get("done") == 6
          and slides[-1].get("total") == 6, json.dumps(slides[-1])[:120])

    detail = client.get(f"/api/presentations/deck/{deck_id}").json()
    check("deck can be resumed/refetched", detail["complete"] is True
          and len(detail["slides"]) == 6)

    retry = client.post("/api/presentations/slide",
                        json={"deck_id": deck_id, "index": 2,
                              "feedback": "make it shorter"})
    check("single slide retry with feedback", retry.status_code == 200)

    od = detail["outline"]
    pptx = client.post("/api/presentations/pptx", json={"deck_id": deck_id, "theme": "ocean"})
    check("pptx export by deck id", pptx.status_code == 200, str(pptx.status_code))
    if pptx.status_code == 200:
        blob = pptx.content
        check("pptx is a zip container", blob[:2] == b"PK")
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = [n for n in zf.namelist() if n.startswith("ppt/slides/slide")]
        check("pptx has one slide per outline entry", len(names) >= len(od["slides"]) + 1,
              f"{len(names)} slide parts")
        try:
            from pptx import Presentation

            prs = Presentation(io.BytesIO(blob))
            text = "\n".join(sh.text_frame.text for s in prs.slides for sh in s.shapes
                             if getattr(sh, "has_text_frame", False))
            check("pptx opens and contains slide text", "Point 1" in text, text[:80])
        except ImportError:
            print("[SKIP] python-pptx not installed here")

    html = client.post("/api/presentations/html", json={"deck_id": deck_id, "theme": "ocean"})
    check("html deck export", html.status_code == 200 and "Point 1" in html.text,
          f"{html.status_code} {len(html.text)} bytes")

    # ------------------------------------------------------------- voice
    voices = client.get("/api/voice/voices").json()
    ids = [v["id"] for v in voices["voices"]]
    check("voice catalogue exposes neural voices", len(ids) >= 4 and "nova" in ids,
          ", ".join(ids))
    check("voice catalogue documents the free-tier caveat",
          "short waits" in voices["note"].lower())
    tts = client.post("/api/voice/tts", json={"text": "Hello there.", "voice": "nova"})
    check("keyless tts returns audio", tts.status_code == 200
          and tts.headers["content-type"] == "audio/mpeg", str(tts.status_code))

    async def failing_tts(text, voice="nova", **kwargs):
        raise keyless.KeylessError("busy", retry_after=7)

    original = keyless.tts_audio
    keyless.tts_audio = failing_tts
    try:
        fallback = client.post("/api/voice/tts", json={"text": "Hello there."})
        payload = fallback.json().get("detail", {})
        check("tts failure asks for the browser fallback",
              fallback.status_code == 503 and payload.get("browser_tts") is True,
              f"{fallback.status_code} {payload}")
        check("tts failure suggests a retry delay",
              float(payload.get("retry_after", 0)) > 0, str(payload.get("retry_after")))
    finally:
        keyless.tts_audio = original

    # --------------------------------------------------------------- admin/status
    status = client.get("/api/ai/status").json()
    poll = [p for p in status["providers"] if p["kind"] == "pollinations"]
    check("keyless provider always present", bool(poll))
    check("status exposes the keyless queue", "queue" in (poll[0] if poll else {}))
    check("status exposes storage health", status["database"]["persistent"] is True
          or status["database"]["warning"] != "")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all offline checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
