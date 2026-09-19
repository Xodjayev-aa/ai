# Aether — multi-AI assistant PWA

Chat with the best answer, not just the fastest one: Aether drafts with one
AI, then a second AI polishes the answer when the question deserves it.
Includes voice input/output, image generation, and presentation building
(view in-app or export real `.pptx`). Runs free on Vercel + Turso + Groq/Cerebras.

## Features

| Feature | How it works |
|---|---|
| 💬 Smart chat | Draft (one provider) → optional polish (a *different* key/provider) → streamed via SSE |
| 🚫 **Zero-key mode** | **Works with NO API keys at all**: chat via keyless Pollinations, research via Wikipedia + DuckDuckGo, voice via browser speech, images via Pollinations |
| 🔑 Key rotation | Round-robins Gemini + 2× Cerebras + Groq; 429 → cooldown → next key; 401 → key disabled. **One `GEMINI_API_KEY` alone powers chat + speech-to-text + text-to-speech** |
| 🎤 Voice in | Browser `MediaRecorder` → Groq Whisper → Gemini native audio (fallback chain) |
| 🔊 Voice out | Groq TTS → Gemini TTS → browser `speechSynthesis` (3-tier fallback) |
| 🖼️ Images | Pollinations.ai (free, keyless) |
| 📊 Presentations | AI writes slide JSON → in-app web slides + `.pptx` export (python-pptx) |
| 👥 Multi-user | JWT + httpOnly cookie auth, login throttling, per-user daily limits |
| 🛠️ Admin dashboard | In-app UI: live stats, user management (promote/demote/delete, owner protected), conversation moderation, provider health |
| 📚 Research mode | Real information from Wikipedia + DuckDuckGo with citations — **no AI generation at all** |
| 🗄️ Storage | SQLite locally, Turso (HTTP SQLite) on Vercel — same code, auto-detected |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add GROQ_API_KEY etc. — never commit .env
python run.py          # http://localhost:8000
```

First registered account whose email equals `OWNER_EMAIL` becomes admin.

## API overview

```
POST /api/auth/register|login      GET /api/auth/me
GET/POST /api/conversations        GET/PATCH/DELETE /api/conversations/{id}
GET /api/conversations/{id}/messages
POST /api/chat/stream              (SSE: phase/delta/final/done events)
POST /api/voice/transcribe         (multipart audio)
POST /api/voice/tts                (text -> wav, 503 => use browser TTS)
POST /api/images/generate
POST /api/presentations/generate   (topic -> slide JSON)
POST /api/presentations/pptx       (slide JSON -> .pptx download)
GET  /api/ai/status                GET /api/health
GET  /api/admin/stats              (admin only)
```

## Configuration

All config is env-driven — see `.env.example`. Deployment guide: [DEPLOY.md](DEPLOY.md).

## Security notes

- API keys live ONLY in environment variables (local `.env` / Vercel settings).
- Passwords are bcrypt-hashed; sessions are signed JWTs (7 days).
- Per-user daily request limit protects the free tiers.
