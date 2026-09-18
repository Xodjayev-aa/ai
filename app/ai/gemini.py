"""Google Gemini native (non-OpenAI) API helpers for speech.

A single GEMINI_API_KEY covers:
  - speech-to-text: audio as inline_data to gemini-2.5-flash
  - text-to-speech: gemini-2.5-flash-preview-tts returning raw PCM (L16,
    24 kHz mono), which we wrap into a proper WAV container in-process.
"""

import base64
import io
import wave

import httpx

from app.ai import config

_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
_SAMPLE_RATE = 24000


class GeminiSpeechError(RuntimeError):
    """Raised with a short, safe message for the caller to surface."""


def _endpoint(model: str, key: str) -> str:
    return f"{_API_ROOT}/{model}:generateContent?key={key}"


def pcm_to_wav(pcm: bytes, sample_rate: int = _SAMPLE_RATE,
               channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw headerless PCM (L16) into a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _extract_text(payload: dict) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError):
        return ""


async def transcribe(audio: bytes, mime: str = "audio/webm") -> str:
    """Speech-to-text via Gemini multimodal input."""
    if not config.GEMINI_API_KEY:
        raise GeminiSpeechError("GEMINI_API_KEY not set")
    body = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime,
                                 "data": base64.b64encode(audio).decode()}},
                {"text": "Transcribe this audio exactly. "
                         "Output ONLY the transcription text."},
            ],
        }],
        "generationConfig": {"temperature": 0},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                _endpoint(config.GEMINI_STT_MODEL, config.GEMINI_API_KEY),
                json=body,
            )
    except httpx.HTTPError as exc:
        raise GeminiSpeechError(f"speech service unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise GeminiSpeechError(f"gemini stt failed ({resp.status_code})")
    text = _extract_text(resp.json())
    if not text:
        raise GeminiSpeechError("gemini stt returned no text (audio too short?)")
    return text


async def tts(text: str) -> bytes:
    """Text-to-speech via Gemini TTS. Returns WAV bytes."""
    if not config.GEMINI_API_KEY:
        raise GeminiSpeechError("GEMINI_API_KEY not set")
    body = {
        "contents": [{"parts": [{"text": text[:config.MAX_TTS_CHARS]}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": config.GEMINI_TTS_VOICE}
                }
            },
        },
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                _endpoint(config.GEMINI_TTS_MODEL, config.GEMINI_API_KEY),
                json=body,
            )
    except httpx.HTTPError as exc:
        raise GeminiSpeechError(f"speech service unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise GeminiSpeechError(f"gemini tts failed ({resp.status_code})")
    try:
        parts = resp.json()["candidates"][0]["content"]["parts"]
        pcm = None
        for p in parts:
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data"):
                pcm = base64.b64decode(inline["data"])
                break
        if not pcm:
            raise KeyError("no inline audio")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GeminiSpeechError("gemini tts returned no audio") from exc
    return pcm_to_wav(pcm)
