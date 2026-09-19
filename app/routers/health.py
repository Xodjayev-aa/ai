from fastapi import APIRouter

from app.ai import client, config
from app.ai.rotator import pool
from app.db.engine import MODE
from app.database import count_users

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.get("/ai/status")
async def ai_status():
    """Which providers/keys are configured and how healthy they are.
    Key values are masked — safe to show in the UI."""
    providers = pool.status()
    return {
        "database_mode": MODE,
        "providers_configured": len(providers),
        "providers": providers,
        "features": config.feature_coverage(),
        "users": count_users(),
    }
