"""Run the real Aether API with stubbed free-tier providers.

CI and the build sandbox have no internet, so the keyless Pollinations calls
(and the keyless Edge HD voices) can never succeed there. This entry point
imports the *production* app and replaces only the provider functions (chat
stream, completion, TTS, HD TTS) with deterministic stubs, which lets the
browser-level E2E test exercise every happy path — streaming, stop/partial,
regenerate, incremental decks, exports, voice, exam mode — over real HTTP,
real auth and a real database.

It also exposes ``GET /__mock/last_prompt`` (test-only) so the E2E can assert
what the server actually put in the system prompt.

    AETHER_MOCK_PORT=8001 .venv/bin/python tests/mock_server.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "/tmp/aether-mock.db")

import app.ai.keyless as keyless  # noqa: E402

WORDS_PER_CHUNK = 3
DELAY = float(os.getenv("AETHER_MOCK_DELAY", "0.05"))


CAPTURED: dict[str, str] = {}          # last system prompt the model received


def _answer_for(messages: list[dict]) -> str:
    """Deterministic, topic-aware-looking text — enough to assert on."""
    last = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            last = str(message.get("content") or "")
            break
    system = " ".join(str(m.get("content") or "") for m in messages
                      if m.get("role") == "system")
    if "you are aether" in system.lower():
        CAPTURED["system"] = system          # ignore memory/aux prompts
    lowered = system.lower()
    if "design the structure" in lowered or "slide titles" in lowered:
        # The plan prompt asks for exactly N titles — honour it so the UI's
        # progress bar is exercised for real.
        match = re.search(r"exactly (\d+) slide titles", system)
        count = int(match.group(1)) if match else 3
        titles = ["Meet the bee", "Why bees matter", "How to help", "Pollination basics",
                  "A hive at work", "What threatens bees", "Gardens that help",
                  "Everyday actions", "Seasons of a bee", "Bees and our food",
                  "A short history", "Myths to drop", "Tools for gardeners",
                  "Community projects", "Measuring impact", "How to teach others",
                  "Questions to ask", "Further reading", "Key takeaways", "Thank you"]
        chosen = titles[:count]
        return json.dumps({"title": "Honey Bees", "subtitle": "Small insects, big job",
                           "slides": [{"title": t} for t in chosen]})
    if "one slide" in lowered:
        return json.dumps({"title": "Why bees matter",
                           "bullets": ["They pollinate a third of our food",
                                       "A hive sustains thousands of lives",
                                       "Small gardens help a lot"],
                           "notes": "Keep it short and warm."})
    if "caption" in system.lower() or "image prompt" in system.lower():
        return "a calm dark charcoal poster with a soft geometric hexagon"
    return (f"You asked: {last[:80]}. Here is a short, honest answer — the free shared tier "
            "keeps replies brief, and you can always ask for more detail afterwards.")


async def stream_text(messages, *, model=None, temperature=0.7, max_tokens=None,
                      used=None, timeout=None):
    text = _answer_for(list(messages))
    words = text.split(" ")
    for index in range(0, len(words), WORDS_PER_CHUNK):
        await asyncio.sleep(DELAY)
        chunk = " ".join(words[index:index + WORDS_PER_CHUNK])
        yield chunk if index == 0 else " " + chunk


async def complete_text(messages, *, model=None, temperature=0.5, max_tokens=None,
                        timeout=None):
    return "".join([chunk async for chunk in stream_text(messages, temperature=temperature)])


async def tts_audio(text: str, voice: str = "nova", *, timeout: float = 45.0,
                    max_chars: int | None = None) -> bytes:
    """A tiny but valid MP3-ish payload: the browser only needs bytes+200."""
    await asyncio.sleep(0.02)
    header = b"ID3\x03\x00\x00\x00\x00\x00\x00"
    body = (f"voice={voice};text={text[:60]}".encode() * 40)[:2000]
    return header + body + bytes(512)


keyless.stream_text = stream_text
keyless.complete_text = complete_text
keyless.tts_audio = tts_audio

# HD (Edge) voices: no network in CI. Stub the websocket call and pre-seed the
# catalogue cache so even the language list is instant (the real code path is
# used everywhere else — including the POPULAR fallback shape).
import app.ai.tts_hd as tts_hd  # noqa: E402


async def hd_synthesize(self, text, *, voice=None, rate=None, pitch=None, volume=None):
    await asyncio.sleep(0.02)
    selected = voice or self.default_voice
    payload = (f"hd;voice={selected};rate={rate};text={text[:60]}".encode() * 60)[:4000]
    return b"ID3\x03\x00\x00\x00\x00\x00\x00" + payload + bytes(1024)


tts_hd.HDTTS.synthesize = hd_synthesize

from app.main import app  # noqa: E402  (import after patching)
from app.routers.voice import hd_tts     # noqa: E402  (the app's singleton)

# Seed the HD catalogue from the built-in POPULAR list (same shapes the live
# VoicesManager produces) so the first /api/voice/voices call is instant.
hd_tts._catalog = [
    {"short": short, "name": name,
     "locale": short.rsplit("-", 1)[0] if "-" in short else short,
     "lang": short.split("-")[0].lower(),
     "gender": "female" if "female" in name.lower() else "male"}
    for short, name in tts_hd.POPULAR
]
hd_tts._catalog_at = time.monotonic()


# The app mounts the built frontend at "/", which would swallow this path, so
# the test-only route is inserted *before* every other route.
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402


async def _last_prompt(_request):
    return JSONResponse({"system": CAPTURED.get("system", "")})


app.router.routes.insert(0, Route("/__mock/last_prompt", _last_prompt))

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("AETHER_MOCK_PORT", "8001"))
    print(f"[mock] serving the real app with stubbed providers on :{port} "
          f"(delay={DELAY}s, started {time.strftime('%H:%M:%S')})", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
