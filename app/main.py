import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.db.engine import kick_warmer, start_recovery, start_warmer

# Never let a broken database stop the app from booting: the UI shows an
# honest storage banner and the engine retries the schema on first use.
if not init_db():
    import logging

    logging.getLogger("aether").error(
        "Database schema initialisation failed — see /api/ai/status for details")

# Cold Turso is the usual culprit behind "the first login after a deploy
# failed": warm it in the background (SELECT 1 at ~0/5/15/30/60 s) so the
# first real query is not also the first round-trip. Never blocks boot.
start_warmer()

# If boot degraded to /tmp while Turso is configured, start probing for its
# return (every ~30 s). No-op when Turso is healthy or not configured.
start_recovery()

app = FastAPI(title="Aether PWA API", version="4.0.0")


class TursoWarmKick:
    """First request after boot: make sure the background warmer is running.

    A plain ASGI middleware (not BaseHTTPMiddleware) so streaming responses —
    every chat answer is SSE — pass through completely untouched. `kick_warmer()`
    only spawns a daemon thread and returns: it is never awaited and never on
    the critical path of the request.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            kick_warmer()
        await self.app(scope, receive, send)


app.add_middleware(TursoWarmKick)

_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
_wildcard = "*" in _origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _wildcard else _origins,
    # Browsers reject `*` together with credentialed requests; we use
    # Bearer tokens (not cookies), so credentials are only enabled when
    # explicit origins are configured.
    allow_credentials=not _wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import admin, auth, chat, conversations, files, health, \
    images, news, presentations, research, settings, share, tasks, teach, \
    usage, voice  # noqa: E402

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(conversations.search_router)
app.include_router(files.router)
app.include_router(health.router)
app.include_router(admin.router)
app.include_router(images.router)
app.include_router(presentations.router)
app.include_router(research.router)
app.include_router(settings.router)
app.include_router(share.router)
app.include_router(news.router)
app.include_router(tasks.router)
app.include_router(tasks.cron_router)
app.include_router(voice.router)
app.include_router(usage.router)
app.include_router(teach.router)

# Serve the PWA from the API process itself. This is the ONLY serving path:
# locally (python run.py) and on Vercel, where vercel.json routes every
# request — including /assets/* and /icons/* — to this function. Keeping one
# code path means what the tests verify is exactly what production serves
# (the platform's static layer 500'd on /icons/* in prod while every local
# and CI check of the files passed — so the files are no longer on the
# platform's path at all).
_FRONTEND = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_FRONTEND):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")

