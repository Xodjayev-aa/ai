# Deploying Aether (free)

**Good news: the site works with ZERO API keys** — you can deploy with just
`APP_SECRET` + `OWNER_EMAIL` and everything functions (chat via keyless
Pollinations, 📚 Research mode via Wikipedia/DuckDuckGo, images via
Pollinations, voice via the browser). The keys below add *capacity and
quality* for many users.

You need **3 signups** and **6 environment variables** for the recommended
setup. Everything else was already built and tested for you.

## The 3 signups (≈10 minutes total)

### 1. Google AI Studio — ONE key that powers everything ⭐
- Go to **https://aistudio.google.com** → sign in with any Google account
- Click **"Get API key"** → **"Create API key"** → copy it
- ✅ No credit card. ✅ Permanent free tier. ✅ No expiry.
- This single key powers: **chat, polish, speech-to-text AND text-to-speech**
  (images are free without any key via Pollinations)

### 2. Turso — the persistent database
- Go to **https://turso.tech** → **Get Started** → sign in with GitHub
- Create a database (call it `aether`), then open it and find:
  - **Database URL** (looks like `https://aether-xxx.turso.io`)
  - **Create token** button → copy the token
- (CLI equivalent: `turso db create aether` → `turso db show aether --http-url`
  → `turso db tokens create aether`)

### 3. Vercel — hosting
- Go to **https://vercel.com** → **Sign Up with GitHub** → allow access
- **Add New… → Project** → import the `ai` repo → click **Deploy**
  (it will fail or warn about missing env vars — that's fine, next step fixes it)

## The 6 environment variables

Vercel → your project → **Settings → Environment Variables** → add each one
(paste values directly here — never share them in chat):

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | your Google AI Studio key |
| `TURSO_DATABASE_URL` | `https://aether-xxx.turso.io` |
| `TURSO_AUTH_TOKEN` | the Turso token |
| `APP_SECRET` | any long random string (see below) |
| `OWNER_EMAIL` | your email — this account auto-becomes admin |
| `ALLOWED_ORIGINS` | `https://your-project.vercel.app` |

Generate `APP_SECRET` at https://www.random.org/strings/ (use 64+ chars,
alphanumeric) or locally: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`

Then **Deployments → ⋯ → Redeploy**. Open your site, register with your
`OWNER_EMAIL` — you're the admin. Done. 🎉

## Optional upgrades (only if you outgrow the free tier)

| What | Do this |
|---|---|
| More chat capacity (recommended for ~150 people) | Add `GROQ_API_KEY` from https://console.groq.com (free, no card) and/or 2× `CEREBRAS_API_KEY_1/2` from https://cloud.cerebras.ai (may need a card). All keys join the automatic rotation. |
| Faster Whisper transcription | Add `GROQ_API_KEY` (used first for STT; Gemini is the fallback). |

## Honest capacity notes for ~150 users

- All free tiers combined give roughly **1,500–3,000 AI requests/day**
  (Gemini Flash ~250–1,000/day per model + Groq ~14k requests/day on small
  models + Cerebras per-key allowances, and the app rotates across all of
  them automatically, cooling down whichever key hits a limit).
- The app already: rotates keys, fails over on errors, caps each user at
  `DAILY_REQUEST_LIMIT` (default 300), and only runs the second "polish" AI
  when the answer needs it. For a class/school-size group that's usually fine.
- If you hit the ceiling: raising `DAILY_REQUEST_LIMIT` won't create quota —
  the real fix is adding more keys (they all stack) or enabling paid tiers.

## Verify the deployment

- `https://your-project.vercel.app/api/health` → `{"status":"ok"}`
- `https://your-project.vercel.app/api/ai/status` → shows every configured
  provider (keys masked), feature coverage, and user count.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in keys locally (git-ignored)
python run.py             # http://localhost:8000 — API + PWA together
```
