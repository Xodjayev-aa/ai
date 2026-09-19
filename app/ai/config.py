"""Central env-driven configuration for AI providers.

Keys are read from environment variables ONLY — never hardcode them.
On Vercel set these in Project Settings → Environment Variables;
locally put them in `.env` (git-ignored).

Provider priority for chat rotation (first configured wins round-robin start):
    Gemini → Cerebras key 1 → Cerebras key 2 → Groq
A single GEMINI_API_KEY alone powers chat + speech-to-text + text-to-speech.
"""

import os


def _clean(key: str) -> str:
    return (os.getenv(key) or "").strip()


# ---------------------------------------------------------------- keys
GEMINI_API_KEY = _clean("GEMINI_API_KEY")
GROQ_API_KEY = _clean("GROQ_API_KEY")
CEREBRAS_API_KEY_1 = _clean("CEREBRAS_API_KEY_1")
CEREBRAS_API_KEY_2 = _clean("CEREBRAS_API_KEY_2")

# ---------------------------------------------------------------- models
# Sensible defaults; override with env vars if provider catalogs change.
GEMINI_BASE_URL = (_clean("GEMINI_BASE_URL")
                   or "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
GEMINI_MODEL = _clean("GEMINI_MODEL") or "gemini-2.5-flash"
GEMINI_FAST_MODEL = _clean("GEMINI_FAST_MODEL") or "gemini-2.5-flash-lite"

GROQ_MODEL = _clean("GROQ_MODEL") or "llama-3.3-70b-versatile"
GROQ_FAST_MODEL = _clean("GROQ_FAST_MODEL") or "llama-3.1-8b-instant"
CEREBRAS_MODEL = _clean("CEREBRAS_MODEL") or "llama-3.3-70b"
CEREBRAS_FAST_MODEL = _clean("CEREBRAS_FAST_MODEL") or "llama-3.1-8b"

GROQ_BASE_URL = (_clean("GROQ_BASE_URL") or "https://api.groq.com/openai/v1").rstrip("/")
CEREBRAS_BASE_URL = (_clean("CEREBRAS_BASE_URL") or "https://api.cerebras.ai/v1").rstrip("/")

# ---------------------------------------------------------------- speech
STT_MODEL = _clean("STT_MODEL") or "whisper-large-v3-turbo"
GEMINI_STT_MODEL = _clean("GEMINI_STT_MODEL") or "gemini-2.5-flash"
GEMINI_TTS_MODEL = _clean("GEMINI_TTS_MODEL") or "gemini-2.5-flash-preview-tts"
GEMINI_TTS_VOICE = _clean("GEMINI_TTS_VOICE") or "Kore"
TTS_MODEL = _clean("TTS_MODEL") or "playai-tts"
TTS_VOICE = _clean("TTS_VOICE") or "Fritz-PlayAI"
MAX_TTS_CHARS = int(_clean("MAX_TTS_CHARS") or "800")

# ---------------------------------------------------------------- images
POLLINATIONS_TOKEN = _clean("POLLINATIONS_TOKEN")  # optional, works without
POLLINATIONS_BASE_URL = (_clean("POLLINATIONS_BASE_URL")
                         or "https://text.pollinations.ai").rstrip("/")
IMAGE_MODEL = _clean("IMAGE_MODEL") or "flux"

# ---------------------------------------------------------------- limits
DAILY_REQUEST_LIMIT = int(_clean("DAILY_REQUEST_LIMIT") or "300")
MAX_HISTORY_MESSAGES = int(_clean("MAX_HISTORY_MESSAGES") or "20")
CHAT_TIMEOUT_SECONDS = float(_clean("CHAT_TIMEOUT_SECONDS") or "60")
MAX_MESSAGE_CHARS = int(_clean("MAX_MESSAGE_CHARS") or "8000")

# Security: bcrypt silently truncates beyond 72 bytes — reject earlier.
MAX_PASSWORD_BYTES = 72


def key_label(key: str) -> str:
    """Safe display form of an API key, e.g. 'gsk_…4f2a'."""
    if not key:
        return ""
    return f"{key[:4]}…{key[-4:]}" if len(key) > 10 else "…"


def configured_providers() -> list[dict]:
    out = []
    if GEMINI_API_KEY:
        out.append({
            "name": "gemini",
            "kind": "gemini",
            "base_url": GEMINI_BASE_URL,
            "key": GEMINI_API_KEY,
            "model": GEMINI_MODEL,
            "fast_model": GEMINI_FAST_MODEL,
        })
    for i, key in enumerate((CEREBRAS_API_KEY_1, CEREBRAS_API_KEY_2), 1):
        if key:
            out.append({
                "name": f"cerebras-{i}",
                "kind": "cerebras",
                "base_url": CEREBRAS_BASE_URL,
                "key": key,
                "model": CEREBRAS_MODEL,
                "fast_model": CEREBRAS_FAST_MODEL,
            })
    if GROQ_API_KEY:
        out.append({
            "name": "groq",
            "kind": "groq",
            "base_url": GROQ_BASE_URL,
            "key": GROQ_API_KEY,
            "model": GROQ_MODEL,
            "fast_model": GROQ_FAST_MODEL,
        })
    # ALWAYS available: keyless Pollinations guarantees the app works
    # with zero API keys configured (free tier, ~1 req / 15s).
    out.append({
        "name": "pollinations-free",
        "kind": "pollinations",
        "base_url": POLLINATIONS_BASE_URL,
        "key": "pollinations-anon",
        "model": "openai",
        "fast_model": "openai",
    })
    return out


def feature_coverage() -> dict:
    """Which capabilities are available with the current configuration."""
    return {
        "chat": [p["name"] for p in configured_providers()],
        "research_mode": ["wikipedia", "duckduckgo"],   # keyless, no AI
        "speech_to_text": (["groq-whisper"] if GROQ_API_KEY else [])
                          + (["gemini"] if GEMINI_API_KEY else [])
                          + ["browser-speech"],          # keyless fallback
        "text_to_speech": (["groq-playai"] if GROQ_API_KEY else [])
                          + (["gemini"] if GEMINI_API_KEY else [])
                          + ["pollinations-audio", "browser-voice"],
        "images": ["pollinations"],  # keyless
        "voice_ready": True,
    }
