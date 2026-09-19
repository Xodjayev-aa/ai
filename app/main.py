import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db

init_db()

app = FastAPI(title="Aether PWA API", version="2.0.0")

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
    images, news, presentations, research, settings, share, tasks, voice  # noqa: E402

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

# Serve the PWA directly from the API for local dev (python run.py).
# On Vercel, static hosting + route rules handle this instead.
_FRONTEND = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_FRONTEND):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")

