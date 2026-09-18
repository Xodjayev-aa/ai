"""Admin endpoints — all guarded by _require_admin (OWNER_EMAIL or is_admin)."""

import os

from fastapi import APIRouter, Depends, HTTPException

from app.ai.rotator import pool  # noqa: F401 (kept for extension)
from app.database import (
    admin_delete_conversation, all_conversations, delete_user, get_user_by_id,
    list_users, set_admin, usage_stats,
)
from app.deps import get_current_user
from app.routers.health import ai_status

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(user):
    owner_email = os.getenv("OWNER_EMAIL", "")
    if user["email"] != owner_email.lower() and not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access denied")
    return user


def _is_owner(user) -> bool:
    owner_email = os.getenv("OWNER_EMAIL", "").strip().lower()
    return bool(owner_email) and user["email"] == owner_email


@router.get("/info")
def admin_info(user=Depends(get_current_user)):
    _require_admin(user)
    return {"owner": os.getenv("OWNER_EMAIL", ""), "status": "authorized"}


@router.get("/stats")
def admin_stats(user=Depends(get_current_user)):
    _require_admin(user)
    return usage_stats()


@router.get("/users")
def admin_list_users(user=Depends(get_current_user)):
    _require_admin(user)
    return list_users()


@router.delete("/users/{user_id}")
def admin_delete_user(user_id: int, user=Depends(get_current_user)):
    me = _require_admin(user)
    if user_id == me["id"]:
        raise HTTPException(status_code=400, detail="You cannot delete yourself.")
    rows = get_user_by_id(user_id)
    if not rows:
        raise HTTPException(status_code=404, detail="User not found")
    if _is_owner(rows):
        raise HTTPException(status_code=403, detail="The owner account cannot be deleted.")
    delete_user(user_id)
    return {"status": "deleted", "id": user_id}


@router.post("/users/{user_id}/admin")
def admin_set_admin(user_id: int, body: dict, user=Depends(get_current_user)):
    me = _require_admin(user)
    rows = get_user_by_id(user_id)
    if not rows:
        raise HTTPException(status_code=404, detail="User not found")
    if _is_owner(rows):
        raise HTTPException(status_code=403, detail="The owner's role is fixed.")
    if user_id == me["id"]:
        raise HTTPException(status_code=400, detail="You cannot change your own role.")
    make_admin = bool(body.get("is_admin"))
    set_admin(user_id, make_admin)
    return {"status": "updated", "id": user_id, "is_admin": make_admin}


@router.get("/conversations")
def admin_all_conversations(user=Depends(get_current_user)):
    _require_admin(user)
    return all_conversations()


@router.delete("/conversations/{conv_id}")
def admin_delete_conversation_route(conv_id: str, user=Depends(get_current_user)):
    _require_admin(user)
    if admin_delete_conversation(conv_id) == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}


@router.get("/overview")
async def admin_overview(user=Depends(get_current_user)):
    """Everything the admin dashboard needs in one call."""
    _require_admin(user)
    ai = await ai_status()
    ai["owner"] = os.getenv("OWNER_EMAIL", "").strip().lower()
    return {
        "stats": usage_stats(),
        "users": list_users(),
        "conversations": all_conversations(limit=100),
        "ai": ai,
    }
