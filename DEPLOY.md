# Deploying Aether (free, keyless)

There are **no API keys to get**. Chat, voice, images and presentations all run
on free shared services, so a deployment is: import the repo into Vercel, then
optionally add Turso for persistence.

## 1. Vercel — hosting

- **https://vercel.com** → **Sign Up with GitHub** → allow access
- **Add New… → Project** → import the `ai` repo → **Deploy**
- The build just works: `vercel.json` routes `/api/*` to the Python function
  and everything else to the static PWA in `frontend/`.

## 2. Turso — the persistent database (recommended, free)

Without it the app still runs, but on Vercel the fallback storage lives in
`/tmp`: accounts and chats can disappear when an instance restarts. The UI shows
an honest warning banner whenever that is the case.

- **https://turso.tech** → **Get Started** → sign in with GitHub
- Create a database (`aether`) and copy:
  - **Database URL** — looks like `https://aether-xxx.turso.io`
  - **Create token** → the auth token
- CLI equivalents: `turso db create aether`, `turso db show aether --http-url`,
  `turso db tokens create aether`

## 3. Environment variables (all optional)

Vercel → project → **Settings → Environment Variables**:

| Name | Value |
|---|---|
| `APP_SECRET` | A long random string — signs session cookies. **Worth setting**: without it the key is derived from `TURSO_AUTH_TOKEN`, or generated per instance, so logins reset whenever the server restarts (`/api/ai/status` reports which case you are in). |
| `TURSO_DATABASE_URL` | `https://aether-xxx.turso.io` |
| `TURSO_AUTH_TOKEN` | the Turso token |
| `OWNER_EMAIL` | your email — that account auto-becomes admin |
| `ALLOWED_ORIGINS` | `https://your-project.vercel.app` (default `*`) |
| `DAILY_REQUEST_LIMIT` | per-user daily AI requests (default 300) |
| `CRON_SECRET` | protects `/api/cron/daily`; Vercel sends it automatically on cron calls |

Generate `APP_SECRET` locally with
`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
**Never paste secrets into chat** — put them straight into Vercel.

Then **Deployments → ⋯ → Redeploy**. Open the site and register with
`OWNER_EMAIL` to become the admin.

## Honest capacity notes

- Every AI call goes through one shared free tier. It is sized for a friendly
  crowd (a class, a club, a small team) — **not** for unlimited public traffic.
- The app already: paces requests through a queue with backoff, shows queue
  position and a live cooldown countdown, caps each user at
  `DAILY_REQUEST_LIMIT`, keeps partial answers when you press Stop, and retries
  with jitter instead of failing silently.
- The copy in the UI says exactly this: *free shared AI — short waits possible.*
- If you outgrow it, the fix is more capacity (a paid tier), not a higher
  per-user limit — raising the cap does not create quota.

## Verify a deployment

- `https://your-project.vercel.app/api/health` → `{"status":"ok","version":"4.0.0"}`
- `https://your-project.vercel.app/api/ai/status` → database mode + warnings,
  provider health, queue snapshot, limits, user count
- `https://your-project.vercel.app/icons/favicon.svg` → the current logo

For a full check with zero keys, run the CI suite against the URL:

```bash
python tests/e2e_preview.py --base-url https://your-project.vercel.app
```

It exercises the shell (and every asset it references), auth, streaming chat,
stop/regenerate, teach mode, usage limits, incremental presentations with
`.pptx` + HTML export, images and voice — then prints a pass/fail table.

> Vercel **preview** URLs are protected by SSO by default. To test one, set the
> repo secret `VERCEL_AUTOMATION_BYPASS_SECRET` to your Vercel automation bypass
> secret; the workflow adds the `x-vercel-protection-bypass` header for you.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # optional — the app runs with an empty environment
python run.py             # http://localhost:8000 — API + PWA together
```
