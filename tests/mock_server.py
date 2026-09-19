"""Run the real Aether API with stubbed free-tier providers.

CI and the build sandbox have no internet, so the keyless Pollinations calls
can never succeed there. This entry point imports the *production* app and
replaces only the three provider functions (chat stream, completion, TTS) with
deterministic stubs, which lets the browser-level E2E test exercise every
happy path — streaming, stop/partial, regenerate, incremental decks, exports,
voice — over real HTTP, real auth and a real database.

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


def _answer_for(messages: list[dict]) -> str:
    """Deterministic, topic-aware-looking text — enough to assert on."""
    last = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            last = str(message.get("content") or "")
            break
    system = " ".join(str(m.get("content") or "") for m in messages
                      if m.get("role") == "system")
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

from app.main import app  # noqa: E402  (import after patching)

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("AETHER_MOCK_PORT", "8001"))
    print(f"[mock] serving the real app with stubbed providers on :{port} "
          f"(delay={DELAY}s, started {time.strftime('%H:%M:%S')})", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
