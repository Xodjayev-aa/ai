"""Keyless AI via Pollinations — no API key, ever.

Text:  POST {base}/openai  (OpenAI-compatible, supports SSE streaming)
TTS:   GET  {base}/{text}?model=openai-audio&voice=...  (returns audio/mpeg)

The free tier is rate-limited per server IP (~1 request / 15s at the time of
writing) and every serverless instance shares that IP. That is why every call
here goes through a process-wide pacer (:mod:`app.ai.queue`) which spaces
requests out, queues instead of failing, and cools down after a 429 — and why
callers get a countable "retry in Ns" instead of a silent hang.

Nothing in this module requires a key or an account. ``POLLINATIONS_TOKEN`` is
optional and only used when someone deliberately sets one.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
import typing

import httpx

from app.ai import config
from app.ai.queue import PacerBusy, KeylessPacer, pacer

DEFAULT_TEXT_MODEL = "openai"
TEXT_MODELS = tuple(
    m.strip() for m in (os.getenv("KEYLESS_TEXT_MODELS") or "openai").split(",")
    if m.strip()
)
# Candidates tried in order; the first base that answers is remembered.
BASE_CANDIDATES = (
    config.POLLINATIONS_BASE_URL,  # overridable for tests/mirrors
    "https://text.pollinations.ai",
    "https://gen.pollinations.ai/v1",
)
MAX_ATTEMPTS = int(os.getenv("KEYLESS_MAX_ATTEMPTS") or "3")
BACKOFF_SECONDS = (2.0, 6.0, 14.0)

# TTS runs on the audio service, which has its own (more forgiving) limits, so
# it gets its own pacer with a short interval: in voice mode the first
# sentence must start playing within a second or two.
tts_pacer = KeylessPacer(
    min_interval=float(os.getenv("KEYLESS_TTS_MIN_INTERVAL") or "0.5"),
    max_interval=float(os.getenv("KEYLESS_TTS_MAX_INTERVAL") or "12"),
    max_wait=float(os.getenv("KEYLESS_TTS_MAX_WAIT") or "6"),
)

# Curated neural voices from the openai-audio model. Friendly labels so the
# call UI can offer "Nova — warm & clear" instead of an opaque id.
VOICES: list[dict] = [
    {"id": "nova", "name": "Nova", "tags": "warm, natural", "default": True},
    {"id": "alloy", "name": "Alloy", "tags": "neutral, clear"},
    {"id": "shimmer", "name": "Shimmer", "tags": "bright, friendly"},
    {"id": "coral", "name": "Coral", "tags": "calm, even"},
    {"id": "ash", "name": "Ash", "tags": "deep, steady"},
    {"id": "sage", "name": "Sage", "tags": "soft, thoughtful"},
    {"id": "echo", "name": "Echo", "tags": "crisp, direct"},
    {"id": "onyx", "name": "Onyx", "tags": "low, serious"},
]
VOICE_IDS = {v["id"] for v in VOICES}
DEFAULT_VOICE = "nova"

_working_base: str | None = None
_working_base_at: float = 0.0
_BASE_TTL = 300.0

_PROMO_RE = re.compile(
    r"(?:^|\n)\s*(\[?\s*(?:generated\s+)?(?:by|via|with)?\s*pollinations[^\n]*\]?\s*)$",
    re.IGNORECASE,
)


def keyless_key() -> str:
    return "pollinations-anon"


class KeylessError(RuntimeError):
    """Keyless provider failed after retries (or is cooling down)."""

    def __init__(self, message: str, *, retry_after: float | None = None,
                 retryable: bool = True) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.retryable = retryable


# ------------------------------------------------------------------ helpers

def _sanitize(text: str) -> str:
    """Strip the free tier's trailing promo line, keep everything else."""
    cleaned = text.strip()
    for _ in range(2):
        stripped = _PROMO_RE.sub("", cleaned).strip()
        if stripped == cleaned:
            break
        cleaned = stripped
    return cleaned


def voice_catalogue() -> list[dict]:
    return [dict(v) for v in VOICES]


def normalize_voice(voice: str | None) -> str:
    v = (voice or "").strip().lower()
    return v if v in VOICE_IDS else DEFAULT_VOICE


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after") or ""
    try:
        return float(raw)
    except ValueError:
        return None


async def _resolve_base(force: bool = False) -> str:
    global _working_base, _working_base_at
    if _working_base and not force and time.time() - _working_base_at < _BASE_TTL:
        return _working_base
    for base in BASE_CANDIDATES:
        if not base:
            continue
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(f"{base}/models")
            if resp.status_code == 200:
                _working_base, _working_base_at = base, time.time()
                return base
        except httpx.HTTPError:
            continue
    if _working_base:
        return _working_base  # stale but better than nothing
    raise KeylessError("Free AI service is unreachable right now.",
                       retry_after=5, retryable=True)


def _chat_body(messages: list[dict], model: str, temperature: float,
               stream: bool, max_tokens: int | None) -> dict:
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
        "private": True,
        "referrer": "aether-pwa",
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    return body


def _parse_sse_chunk(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    choices = data.get("choices") or []
    if not choices:
        return None
    choice = choices[0] or {}
    delta = choice.get("delta") or {}
    text = delta.get("content")
    if text is None:
        text = (choice.get("message") or {}).get("content")
    return text or None


# --------------------------------------------------------------------- text

async def stream_text(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    used: list[str] | None = None,
    timeout: float | None = None,
) -> typing.AsyncIterator[str]:
    """Yield content deltas from the keyless OpenAI-compatible endpoint.

    Retries with backoff on rate limits/timeouts *as long as nothing has been
    streamed yet* — once tokens are on the wire we never restart, because a
    restart would duplicate text in the user's answer.
    """
    models = [model] if model else list(TEXT_MODELS)
    total_timeout = timeout or config.CHAT_TIMEOUT_SECONDS
    last_error: str = ""
    attempts = max(1, MAX_ATTEMPTS)

    for attempt in range(attempts):
        try:
            waited = await pacer.acquire()
        except PacerBusy as busy:
            raise KeylessError(str(busy), retry_after=busy.retry_after) from busy
        # `waited` is the queue time; the chat router reports it to the UI
        # before the request is issued (see routers/chat.py).
        _ = waited
        base = await _resolve_base()
        for candidate in models:
            yielded = False
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(total_timeout, connect=12),
                    follow_redirects=True,
                ) as client:
                    async with client.stream(
                        "POST", f"{base}/openai",
                        json=_chat_body(messages, candidate, temperature, True, max_tokens),
                    ) as resp:
                        if resp.status_code != 200:
                            detail = (await resp.aread()).decode(errors="replace")[:200]
                            last_error = f"{resp.status_code}: {detail}"
                            if resp.status_code == 429:
                                pacer.rate_limited(_retry_after(resp))
                                raise KeylessError(
                                    "Free AI is cooling down (shared free tier).",
                                    retry_after=pacer.cooldown_remaining(),
                                )
                            if resp.status_code in (401, 403, 404):
                                # Wrong model/mirror — try the next candidate.
                                continue
                            raise KeylessError(
                                f"Free AI returned {resp.status_code}: {detail[:120]}",
                                retry_after=2,
                            )
                        async for line in resp.aiter_lines():
                            text = _parse_sse_chunk(line)
                            if text:
                                yielded = True
                                yield text
                if yielded:
                    pacer.success()
                    if used is not None:
                        used.append(keyless_key())
                    return
                last_error = "empty response"
            except KeylessError:
                raise
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                pacer.failure()
                last_error = f"{type(exc).__name__}: {exc}"
                if yielded:
                    # Partial answer already delivered — do not restart it.
                    raise KeylessError(
                        "The connection dropped mid-answer.", retry_after=3
                    ) from exc
                continue

        # Every model failed for this attempt: back off and try again.
        pacer.rate_limited(None if "429" in last_error else None)
        if attempt < attempts - 1:
            delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            await asyncio.sleep(delay + random.uniform(0, 1.5))

    raise KeylessError(
        f"Free AI is unavailable right now ({last_error or 'no response'}).",
        retry_after=pacer.cooldown_remaining() or 8,
    )


async def complete_text(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.5,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> str:
    buf: list[str] = []
    async for delta in stream_text(messages, model=model, temperature=temperature,
                                   max_tokens=max_tokens, timeout=timeout):
        buf.append(delta)
    text = _sanitize("".join(buf))
    if not text:
        raise KeylessError("Free AI returned an empty answer.", retry_after=4)
    return text


# ---------------------------------------------------------------------- tts

def _audio_ok(resp: httpx.Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    if resp.status_code != 200:
        return False
    if "audio" in ctype or "mpeg" in ctype or "octet-stream" in ctype:
        return len(resp.content) > 1024
    # Some responses come back as JSON errors with HTTP 200.
    return False


async def tts_audio(
    text: str,
    voice: str = DEFAULT_VOICE,
    *,
    timeout: float = 45.0,
    max_chars: int | None = None,
) -> bytes:
    """Keyless TTS: the openai-audio model reads the text aloud (mp3 bytes).

    Raises :class:`KeylessError` with ``retry_after`` when the free voice
    service is cooling down — the caller then switches to browser voices.
    """
    from urllib.parse import quote

    clean = (text or "").strip()
    if not clean:
        raise KeylessError("Nothing to say.", retryable=False)
    clean = clean[: max_chars or config.MAX_TTS_CHARS]
    voice = normalize_voice(voice)
    try:
        await tts_pacer.acquire()
    except PacerBusy as busy:
        raise KeylessError(str(busy), retry_after=busy.retry_after) from busy

    base = await _resolve_base()
    url = (f"{base}/{quote(clean, safe='')}"
           f"?model=openai-audio&voice={voice}&private=true")
    if config.POLLINATIONS_TOKEN:
        url += f"&token={config.POLLINATIONS_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        tts_pacer.failure()
        raise KeylessError(f"Voice service unreachable: {exc}", retry_after=3) from exc

    if resp.status_code == 429:
        tts_pacer.rate_limited(_retry_after(resp))
        raise KeylessError("Free voice service is busy.",
                           retry_after=tts_pacer.cooldown_remaining() or 5)
    if not _audio_ok(resp):
        tts_pacer.failure()
        raise KeylessError(
            f"Voice service returned {resp.status_code}",
            retry_after=3 if resp.status_code >= 500 else None,
        )
    tts_pacer.success()
    return resp.content


def status_snapshot() -> dict:
    """Queue/cooldown state for the UI (honest capacity reporting)."""
    return {
        "base": _working_base or BASE_CANDIDATES[0],
        "models": list(TEXT_MODELS),
        "queue": pacer.snapshot(),
        "voice": {**tts_pacer.snapshot(), "provider": "pollinations-openai-audio"},
    }
