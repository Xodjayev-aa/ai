"""Voice endpoints with a provider fallback chain.

Speech-to-text:  Groq Whisper → Gemini (native audio) → 422
Text-to-speech:  Groq PlayAI → Gemini TTS → 503 {browser_tts: true}
(the frontend then falls back to the browser's built-in speechSynthesis)
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.ai import config, gemini
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
    return JSONResponse(status_code=503, content={"detail":
        "Speech-to-text unavailable right now (no provider configured or all failed). "
        "Configure GEMINI_API_KEY or GROQ_API_KEY."})


# ------------------------------------------------------------------- tts

class TTSBody(BaseModel):
    text: str


async def _tts_groq(text: str) -> bytes:
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


def _unavailable(text: str) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": {
        "message": text, "browser_tts": True}})


@router.post("/tts")
async def text_to_speech(body: TTSBody, user=Depends(get_current_user)):
    text = body.text.strip()[:config.MAX_TTS_CHARS]
    if not text:
        raise HTTPException(status_code=400, detail="No text to speak")

    for method, available in ((_tts_groq, bool(config.GROQ_API_KEY)),
                              (gemini.tts, bool(config.GEMINI_API_KEY))):
        if not available:
            continue
        try:
            wav = await method(text)
            if wav:
                return Response(content=wav, media_type="audio/wav",
                                headers={"Cache-Control": "no-store"})
        except gemini.GeminiSpeechError:
            continue
    # Keyless fallback: Pollinations openai-audio (free, no key, mp3).
    from app.ai import keyless
    try:
        mp3 = await keyless.tts_audio(text)
        if mp3:
            return Response(content=mp3, media_type="audio/mpeg",
                            headers={"Cache-Control": "no-store"})
    except keyless.KeylessError:
        pass
    # Frontend falls back to the browser's built-in speechSynthesis.
    return _unavailable("Server TTS unavailable — using browser voice instead.")
