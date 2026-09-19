"""Visible fair-use metering: what you have used, what resets when, and
whether the shared free AI is cooling down right now.

The numbers here back the sidebar meter, the Settings panel and the friendly
"Free AI cooling down — retry in 12s" state. They are deliberately honest:
the keyless free tier is shared, so a cooldown is a normal, visible event.
"""

import datetime

from fastapi import APIRouter, Depends

from app.ai import config as ai_config
from app.ai import keyless
from app.db.engine import status as db_status
from app.database import count_users, get_usage
from app.deps import get_current_user

router = APIRouter(prefix="/api", tags=["usage"])


def _reset_at() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.isoformat().replace("+00:00", "Z")


def usage_payload(user) -> dict:
    used = get_usage(user["id"])
    limit = ai_config.DAILY_REQUEST_LIMIT
    queue = keyless.pacer.snapshot()
    cooldown = keyless.pacer.cooldown_remaining()
    return {
        "used_today": used,
        "limit": limit,
        "remaining": max(0, limit - used),
        "resets_at": _reset_at(),
        "utc_now": datetime.datetime.now(datetime.timezone.utc)
                    .isoformat().replace("+00:00", "Z"),
        "cooldown": {
            "active": cooldown > 0.5,
            "seconds": round(cooldown, 1),
            "reason": "Free AI is cooling down (shared free tier).",
        },
        "queue": {
            "estimated_wait_seconds": queue["estimated_wait_seconds"],
            "queued_requests": queue["queued_requests"],
            "interval_seconds": queue["interval_seconds"],
            "requests_last_minute": queue["requests_last_minute"],
        },
        "capacity": {
            "users": count_users(),
            "note": "Free shared AI — short waits possible.",
        },
        "storage": db_status(),
    }


@router.get("/usage")
def usage(user=Depends(get_current_user)):
    return usage_payload(user)
