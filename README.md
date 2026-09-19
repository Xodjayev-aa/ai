# Aether — free keyless AI assistant (PWA)

Chat, voice calls, images and real presentations — **with no API keys, no
signups and no cost**. Everything runs on free shared services: chat, voice and
presentations use the keyless Pollinations tier, research reads Wikipedia and
DuckDuckGo directly, and images come from Pollinations' free generator.

Because that capacity is shared, it is sized for a friendly crowd rather than
unlimited traffic: requests queue, pace themselves, and you will sometimes see
an honest cooldown counter. The UI says so — no "unlimited" promises.

- **Live**: https://aiaether.vercel.app
- **Stack**: FastAPI + Turso/SQLite, vanilla-JS PWA (no build step), Vercel

## Features

| Feature | How it works |
|---|---|
| 💬 Chat | SSE streaming with keep-alive pings, phase labels, stop-keeps-partial, regenerate, edit & resend, follow-up chips, code cards with copy buttons |
| 🔊 **Voice calls** | Full-screen call mode with HD neural voices (~140 languages incl. Uzbek), sentence-by-sentence TTS so the first sound is fast, continuous listening with silence auto-send, barge-in, live captions, mute. Fallback chain: HD → backup voice → your device's voices |
| 🎓 **Speaking exams** | Optional examiner mode in the call: IELTS / CEFR B1 / CEFR B2 / school / your own topic — one question at a time, a short tip after each answer, then a feedback card with an estimated band, three strengths and three concrete fixes |
| 💬 **Language & tone** | Reply in your language (auto or picked), family-friendly by default, and a "strong language" switch for realistic mature dialogue when *you* turn it on |
| 📊 Presentations | Incremental build (outline → slide by slide) with per-slide retry/resume, in-app viewer, `.pptx` **and** standalone HTML export |
| 🖼️ Images | Aspect-ratio picker, download, regenerate, prompt history |
| 🧠 Teach Mode | A 5-question interview plus "Teach Aether" on any answer; memories are editable and injected into future prompts |
| 📈 Honest limits | Usage meter (resets midnight UTC), per-user daily cap, queue position and cooldown countdown with auto-retry |
| 📚 Research mode | Real sources from Wikipedia + DuckDuckGo — no AI generation at all |
| 🔐 Accounts | JWT sessions (httpOnly cookie or Bearer), bcrypt passwords, login throttling |
| 🛠️ Admin | In-app stats, user management, conversation moderation, provider health |
| 🗄️ Storage | Turso over HTTP on Vercel, local SQLite otherwise; self-healing schema, honest warning banner when storage is temporary |

## Quick start (no keys needed)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional: only APP_SECRET / Turso / OWNER_EMAIL
python run.py                 # http://localhost:8000
```

The first account whose email matches `OWNER_EMAIL` becomes admin.

## Configuration

Everything is optional — the app works with an empty environment.

| Variable | Why |
|---|---|
| `APP_SECRET` | Signs session JWTs. **Recommended in production.** Without it the key is derived from `TURSO_AUTH_TOKEN`, or generated per process (logins then reset on restart) — `/api/ai/status` reports which case applies and the UI shows it honestly. |
| `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` | Persistent database on Vercel. Unset → local SQLite file, or `/tmp` on Vercel with a visible warning that data is temporary. |
| `OWNER_EMAIL` | That account is promoted to admin on registration. |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins (default `*`). |
| `DAILY_REQUEST_LIMIT` | Per-user, per-day AI request cap (default 300). |

There is no API-key code path in the product: the assistant never asks for keys.

## API overview

```
POST /api/auth/register|login|logout   GET /api/auth/me   POST /api/auth/password
GET/POST /api/conversations            GET/PATCH/DELETE /api/conversations/{id}
GET  /api/conversations/{id}/messages  POST /api/conversations/{id}/truncate
GET  /api/conversations/{id}/export    GET /api/chats/search?q=
POST /api/chat/stream                  (SSE: meta/phase/ping/delta/final/done/error)
POST /api/chat/partial                 (save a stopped answer)
POST /api/presentations/plan           (outline -> deck_id + slide titles)
POST /api/presentations/slide          (generate/regenerate ONE slide)
GET  /api/presentations/decks          GET/DELETE /api/presentations/deck/{id}
POST /api/presentations/pptx|html      (deck_id -> file download)
POST /api/images/generate              GET /api/images/download?url=
GET  /api/voice/voices                 POST /api/voice/tts
POST /api/voice/transcribe             (503 => use browser speech recognition)
POST /api/teach/{start,answer,finish,improve}
GET  /api/usage                        GET /api/ai/status      GET /api/health
GET  /api/settings/{instructions,memory,personas,export}
GET/PUT /api/settings/prefs            (voice, language, strong-language, exam)
GET  /api/admin/{overview,users,conversations,info}
```

## Verification

Four suites, all runnable offline (the mock server stubs only the free-tier
providers, nothing else):

```bash
rm -f /tmp/aether-test.db && .venv/bin/python tests/test_offline.py   # 43 backend checks
.venv/bin/python -m pytest tests/test_hd_tts.py -q                  # 8 HD-voice unit checks
.venv/bin/python -m pytest tests/test_auth_resilience.py -q         # 9 auth-retry checks
node tests/frontend-smoke.mjs                                       # 99 jsdom checks

# real HTTP against a real server + database
DATABASE_URL=/tmp/aether-preview.db .venv/bin/python -m uvicorn app.main:app --port 8000 &
AETHER_BASE=http://127.0.0.1:8000 node tests/frontend-live.mjs       # 40 checks

# happy paths (streaming, decks, exports, voice) with the free tier stubbed
AETHER_MOCK_PORT=8001 .venv/bin/python tests/mock_server.py &
AETHER_BASE=http://127.0.0.1:8001 node tests/frontend-e2e.mjs        # 58 checks
```

`tests/e2e_preview.py` is the CI suite: it tests a deployment (or a local boot)
end-to-end with zero keys and posts its report on the pull request.

## Deployment

See [DEPLOY.md](DEPLOY.md) — Vercel import plus (optionally) Turso. Zero keys
required; `APP_SECRET` is the only variable worth setting on day one.
