"""Voice endpoints.

Speech-to-text:  Groq Whisper → Gemini (native audio) → 503, in which case the
                 browser's own recognition is used (keyless).
Text-to-speech:  Pollinations openai-audio (keyless neural voices, the
                 default chain) → Groq PlayAI → Gemini TTS → 503
                 {browser_tts: true} so the client falls back to the browser's
                 *neural* voices (never a robotic default).

Voice mode ("call mode") is built on top of this: the client splits its reply
into sentences and asks for them one at a time, so the first sound starts
within a second or two instead of after a long silence.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.ai import config, gemini, keyless
from app.database import bump_usage
from app.deps import get_current_user

router = APIRouter(prefix="/api/voice", tags=["voice"])

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
    voice: str = Field(default=keyless.DEFAULT_VOICE, max_length=32)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


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
def voices():
    """The voices the call UI can offer, plus the browser fallback contract."""
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
    }


@router.post("/tts")
async def text_to_speech(body: TTSBody, user=Depends(get_current_user)):
    """Return spoken audio for one short chunk (a sentence or two).

    The voice call UI deliberately sends *sentences*, not whole answers.
    """
    text = body.text.strip()[:config.MAX_TTS_CHARS]
    if not text:
        raise HTTPException(status_code=400, detail="No text to speak")

    # (a) Keyless Pollinations neural voices — the primary, human-sounding route.
    try:
        mp3 = await keyless.tts_audio(text, body.voice)
        if mp3:
            return Response(content=mp3, media_type="audio/mpeg", headers={
                "Cache-Control": "no-store",
                "X-Voice": keyless.normalize_voice(body.voice),
                "X-Voice-Engine": "pollinations",
            })
    except keyless.KeylessError as exc:
        last_error = str(exc)
        retry_after = exc.retry_after
    else:
        last_error, retry_after = "", None

    # (b) Optional keyed providers, only if someone configured keys.
    for method, available in ((_tts_groq, bool(config.GROQ_API_KEY)),
                              (gemini.tts, bool(config.GEMINI_API_KEY))):
        if not available:
            continue
        try:
            wav = await method(text, body.voice) if method is _tts_groq \
                else await method(text)
            if wav:
                return Response(content=wav, media_type="audio/wav", headers={
                    "Cache-Control": "no-store", "X-Voice-Engine": "keyed",
                })
        except gemini.GeminiSpeechError:
            continue

    # (c) Browser neural voices take over (the client filters for Natural/
    #     Neural/Google/Samantha-class voices).
    headers = {"Retry-After": str(int(max(2, retry_after or 5)))}
    return JSONResponse(status_code=503, headers=headers, content={
        "detail": {
            "message": last_error or "Free voice service is busy.",
            "browser_tts": True,
            "retry_after": round(retry_after or 5, 1),
        }
    })
