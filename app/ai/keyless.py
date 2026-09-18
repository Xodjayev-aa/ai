"""Keyless AI via Pollinations — no API key, ever.

Text:  POST {base}/openai  (OpenAI-compatible, supports SSE streaming)
TTS:   GET  {base}/{text}?model=openai-audio&voice=...  (returns audio/mpeg)

Free tier is rate-limited (~1 request / 15s), so this provider sits at the
END of the rotation pool: it guarantees the app works with ZERO keys
configured, and acts as a last-resort fallback when keyed providers fail.
"""

import re
import typing

import httpx

from app.ai import config

DEFAULT_TEXT_MODEL = "openai"
# Candidates tried in order; the first base that answers is remembered.
BASE_CANDIDATES = (
    config.POLLINATIONS_BASE_URL,  # overridable for tests (default text.pollinations.ai)
    "https://gen.pollinations.ai/v1",
)
_working_base: str | None = None


def keyless_key() -> str:
    return "pollinations-anon"


class KeylessError(RuntimeError):
    pass


async def _resolve_base() -> str:
    global _working_base
    if _working_base:
        return _working_base
    for base in BASE_CANDIDATES:
        if not base:
            continue
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{base}/models")
            if resp.status_code == 200:
                _working_base = base
                return base
        except httpx.HTTPError:
            continue
    raise KeylessError("Pollinations is unreachable right now")


def _sanitize(text: str) -> str:
    # Pollinations free tier may append promo links; strip obvious ones.
    return re.sub(r"\[?https?://\S*pollinations\.\S*?\]?\s*$", "", text.strip(),
                  flags=re.IGNORECASE).strip()


async def stream_text(
    messages: list[dict],
    *,
    model: str = DEFAULT_TEXT_MODEL,
    temperature: float = 0.7,
    used: list[str] | None = None,
) -> typing.AsyncIterator[str]:
    """Yield content deltas from the keyless OpenAI-compatible endpoint."""
    base = await _resolve_base()
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "private": True,
    }
    last_err = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(config.CHAT_TIMEOUT_SECONDS,
                                                      connect=10)) as client:
        try:
            async with client.stream("POST", f"{base}/openai", json=body) as resp:
                if resp.status_code != 200:
                    last_err = f"{resp.status_code}: "
                    last_err += (await resp.aread()).decode(errors="replace")[:200]
                    raise KeylessError(f"keyless provider returned {last_err}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        import json
                        data = json.loads(chunk)
                    except Exception:
                        continue
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise KeylessError(f"keyless provider unreachable: {exc}") from exc
    if used is not None:
        used.append(keyless_key())


async def complete_text(messages: list[dict], *, model: str = DEFAULT_TEXT_MODEL,
                        temperature: float = 0.5) -> str:
    buf: list[str] = []
    async for delta in stream_text(messages, model=model, temperature=temperature):
        buf.append(delta)
    text = _sanitize("".join(buf))
    if not text:
        raise KeylessError("keyless provider returned empty text")
    return text


async def tts_audio(text: str, voice: str = "nova") -> bytes:
    """Keyless TTS: the openai-audio model reads the prompt aloud (mp3)."""
    from urllib.parse import quote

    base = await _resolve_base()
    url = (f"{base}/{quote(text[:config.MAX_TTS_CHARS], safe='')}"
           f"?model=openai-audio&voice={voice}")
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise KeylessError(f"keyless tts unreachable: {exc}") from exc
    ct = resp.headers.get("content-type", "")
    if resp.status_code == 200 and "audio" in ct:
        return resp.content
    raise KeylessError(f"keyless tts failed ({resp.status_code})")
