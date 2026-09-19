from fastapi import APIRouter

from app.ai import client, config, keyless
from app.routers.voice import hd_tts
from app.ai.rotator import pool
from app.db.engine import status as db_status
from app.database import count_users
from app.security import secret_status

router = APIRouter(prefix="/api", tags=["health"])

VERSION = "4.0.0"


@router.get("/health")
def health_check():
    return {"status": "ok", "version": VERSION}


@router.get("/ai/status")
async def ai_status():
    """Which providers/keys are configured and how healthy they are.

    Keyless by default: with zero API keys this still reports a working
    provider (Pollinations free tier) plus its live queue/cooldown state, so
    the UI can be honest about capacity.
    """
    providers = pool.status()
    keyless_state = keyless.status_snapshot()
    for prov in providers:
        if prov.get("kind") == "pollinations":
            prov["queue"] = keyless_state["queue"]
            prov["label"] = "Free shared AI (no key)"
    return {
        "version": VERSION,
        "database_mode": db_status()["mode"],
        "security": secret_status(),
        "hd_tts": hd_tts.status(),
        "database": db_status(),
        "providers_configured": len(providers),
        "providers": providers,
        "features": config.feature_coverage(),
        "keyless": keyless_state,
        "limits": {
            "daily_requests_per_user": config.DAILY_REQUEST_LIMIT,
            "max_message_chars": config.MAX_MESSAGE_CHARS,
            "history_messages": config.MAX_HISTORY_MESSAGES,
        },
        "users": count_users(),
    }
