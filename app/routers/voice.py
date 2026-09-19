"""Voice endpoints.

Speech-to-text:  Groq Whisper → Gemini (native audio) → 503, in which case the
                 browser's own recognition is used (keyless).
Text-to-speech:  HD Edge neural voices (keyless, ~140 languages) → keyless
                 Pollinations openai-audio → optional keyed providers → 503
                 {browser_tts: true} so the client falls back to the browser's
                 *neural* voices (never a robotic default).

Every route in the chain is free and keyless. The HD service is unofficial and
can change, so it runs behind a timeout + circuit breaker and any failure simply
moves down the chain — a voice call must never break.

Voice mode ("call mode") is built on top of this: the client splits its reply
into sentences and asks for them one at a time, so the first sound starts
within a second or two instead of after a long silence.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.ai import config, gemini, keyless
from app.ai import tts_hd
from app.database import bump_usage
from app.deps import get_current_user

router = APIRouter(prefix="/api/voice", tags=["voice"])

# One HDTTS instance per process: it owns the voice catalogue cache, the
# failure streak and the circuit breaker state.
hd_tts = tts_hd.HDTTS()

ALLOWED_AUDIO = {
    "audio/webm", "audio/ogg", "audio/mpeg", "audio/mp3", "audio/mp4",
    "audio/m4a", "audio/x-m4a", "audio/wav", "audio/x-wav", "audio/flac",
    "audio/ogg;codecs=opus", "video/webm", "application/ogg", "audio/mpga",
    "audio/opus",
}
ALLOWED_EXTENSIONS = (".webm", ".ogg", ".mp3", ".m4a", ".wav", ".mp4", ".opus", ".flac")
MAX_AUDIO_BYTES = 24 * 1024 * 1024


def _is_audio(file: UploadFile) -> bool:
    ct = (file.content_type or "").lower().split(";")[0].strip()
    name = (file.filename or "").lower()
    return ct in ALLOWED_AUDIO or name.endswith(ALLOWED_EXTENSIONS)


# ------------------------------------------------------------ transcribe

async def _stt_groq(data: bytes, file: UploadFile) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{config.GROQ_BASE_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            files={"file": (file.filename or "audio.webm", data,
                            file.content_type or "audio/webm")},
            data={"model": config.STT_MODEL, "temperature": "0",
                  "response_format": "json"},
        )
    if resp.status_code != 200:
        raise gemini.GeminiSpeechError(f"groq stt failed ({resp.status_code})")
    return (resp.json().get("text") or "").strip()


@router.post("/transcribe")
async def transcribe(file: UploadFile, user=Depends(get_current_user)):
    if not _is_audio(file):
        raise HTTPException(status_code=400, detail="Unsupported audio format")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio file")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio too large (max ~24 MB)")

    mime = (file.content_type or "audio/webm").lower()
    errors: list[str] = []
    for method, available in ((_stt_groq, bool(config.GROQ_API_KEY)),
                              (gemini.transcribe, bool(config.GEMINI_API_KEY))):
        if not available:
            continue
        try:
            text = await method(data, file) if method is _stt_groq \
                else await method(data, mime.split(";")[0])
            if text:
                bump_usage(user["id"])
                return {"text": text, "provider": method.__name__}
            errors.append(f"{method.__name__}: empty result")
        except gemini.GeminiSpeechError as exc:
            errors.append(str(exc))
    return JSONResponse(status_code=503, content={
        "detail": "Server speech-to-text is unavailable — using the browser's "
                  "own recognition instead.",
        "browser_stt": True,
        "errors": errors[:3],
    })


# ------------------------------------------------------------------- tts

class TTSBody(BaseModel):
    text: str = Field(min_length=1, max_length=1200)
    # Either a short id ("nova") or an Edge ShortName ("uz-UZ-SardorNeural").
    voice: str = Field(default=keyless.DEFAULT_VOICE, max_length=64)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    # auto = HD → legacy → browser; hd = HD only, then fall through the chain;
    # browser = skip the servers entirely (Settings → "browser voices only").
    provider: str = Field(default="auto", pattern="^(auto|hd|browser)$")


def _is_hd_voice(voice: str) -> bool:
    """Edge ShortNames look like 'uz-UZ-SardorNeural' — never a bare 'nova'."""
    return "-" in voice and voice.lower().endswith("neural")


def _hd_rate(speed: float) -> str:
    """Speed multiplier → Edge's '+10%' / '-15%' rate syntax."""
    percent = round((max(0.5, min(2.0, speed)) - 1.0) * 100)
    return f"{percent:+d}%"


async def _tts_groq(text: str, voice: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{config.GROQ_BASE_URL}/audio/speech",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json={"model": config.TTS_MODEL, "voice": config.TTS_VOICE,
                  "input": text, "response_format": "wav"},
        )
    if resp.status_code != 200 or not resp.content:
        raise gemini.GeminiSpeechError(f"groq tts failed ({resp.status_code})")
    return resp.content


@router.get("/voices")
async def voices(refresh: bool = False):
    """The voices the call UI can offer, plus the browser fallback contract.

    ``hd`` carries the HD Edge catalogue (~140 languages) with a popular-first
    language list, so Settings can filter without shipping a static list.
    """
    hd_catalog = await hd_tts.voices(force=refresh)
    return {
        "engine": "pollinations-openai-audio",
        "keyless": True,
        "voices": keyless.voice_catalogue(),
        "default": keyless.DEFAULT_VOICE,
        "browser_fallback": True,
        "speeds": [0.85, 1.0, 1.15, 1.3],
        "note": "Free shared voice servers can have short waits. If they are "
                "busy, Aether switches to your device's best neural voice.",
        "queue": keyless.tts_pacer.snapshot(),
        "hd": {
            "enabled": bool(hd_tts.enabled),
            "default_voice": tts_hd.DEFAULT_VOICE,
            "provider": "edge-neural",
            "languages": await hd_tts.languages(),
            "voices": hd_catalog,
            "note": "HD neural voices are free and keyless; if the service is "
                    "busy Aether automatically uses the backup voice.",
        },
    }


@router.post("/tts")
async def text_to_speech(body: TTSBody, user=Depends(get_current_user)):
    """Return spoken audio for one short chunk (a sentence or two).

    The voice call UI deliberately sends *sentences*, not whole answers, so the
    first sound starts within a second or two.

    Chain: HD Edge neural → keyless Pollinations → keyed (if configured) →
    503 {browser_tts: true}. ``X-Aether-TTS`` reports which link answered.
    """
    text = body.text.strip()[:config.MAX_TTS_CHARS]
    if not text:
        raise HTTPException(status_code=400, detail="No text to speak")

    last_error = ""
    retry_after = None

    # (a) HD Edge neural voices — ~140 languages, best quality, keyless. Routed
    #     through the shared TTS pacer so a burst of sentences stays polite.
    if body.provider != "browser" and _is_hd_voice(body.voice):
        try:
            await keyless.tts_pacer.acquire()
        except Exception:
            pass                     # never block the chain on pacing
        audio = await hd_tts.synthesize(
            text,
            voice=body.voice or tts_hd.DEFAULT_VOICE,
            rate=_hd_rate(body.speed),
        )
        if audio:
            keyless.tts_pacer.success()
            return Response(content=audio, media_type="audio/mpeg", headers={
                "Cache-Control": "no-store",
                "X-Voice": body.voice,
                "X-Aether-TTS": "hd",
                "X-Voice-Engine": "edge-neural",
            })

    # (b) Keyless Pollinations neural voices — the backup, still keyless.
    try:
        mp3 = await keyless.tts_audio(text, body.voice)
        if mp3:
            return Response(content=mp3, media_type="audio/mpeg", headers={
                "Cache-Control": "no-store",
                "X-Voice": keyless.normalize_voice(body.voice),
                "X-Aether-TTS": "legacy",
                "X-Voice-Engine": "pollinations",
            })
    except keyless.KeylessError as exc:
        last_error = str(exc)
        retry_after = exc.retry_after
    except Exception as exc:  # noqa: BLE001 — a busy backup must not 500 a call
        last_error = f"backup voice unavailable ({type(exc).__name__})"

    # (c) Optional keyed providers, only if someone configured keys.
    for method, available in ((_tts_groq, bool(config.GROQ_API_KEY)),
                              (gemini.tts, bool(config.GEMINI_API_KEY))):
        if not available:
            continue
        try:
            wav = await method(text, body.voice) if method is _tts_groq \
                else await method(text)
            if wav:
                return Response(content=wav, media_type="audio/wav", headers={
                    "Cache-Control": "no-store", "X-Aether-TTS": "legacy",
                    "X-Voice-Engine": "keyed",
                })
        except gemini.GeminiSpeechError:
            continue

    # (d) Browser neural voices take over (the client filters for Natural/
    #     Neural/Google/Samantha-class voices).
    headers = {"Retry-After": str(int(max(2, retry_after or 5)))}
    return JSONResponse(status_code=503, headers=headers, content={
        "detail": {
            "message": last_error or "Free voice service is busy.",
            "browser_tts": True,
            "retry_after": round(retry_after or 5, 1),
        }
    })
