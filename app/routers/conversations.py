import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from fastapi.responses import Response

from app.database import (
    create_conversation, delete_conversation, get_conversation,
    list_conversations, list_messages, rename_conversation, search_messages,
    update_conv_meta,
)
from app.deps import get_current_user

router = APIRouter(prefix="/api/conversations", tags=["conversations"])
search_router = APIRouter(prefix="/api/chats", tags=["conversations"])


class ConvCreate(BaseModel):
    title: str = Field(default="New chat", max_length=120)


class ConvRename(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    folder: str | None = Field(default=None, max_length=60)
    pinned: bool | None = None


@search_router.get("/search")
def search(q: str, user=Depends(get_current_user)):
    if len(q.strip()) < 2:
        return []
    return search_messages(user["id"], q.strip())


@router.get("")
def list_all(user=Depends(get_current_user)):
    return list_conversations(user["id"])


@router.post("")
def create(data: ConvCreate, user=Depends(get_current_user)):
    return create_conversation(user["id"], data.title or "New chat")


@router.get("/{conv_id}")
def get_one(conv_id: str, user=Depends(get_current_user)):
    conv = get_conversation(conv_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv = dict(conv)
    conv["messages"] = list_messages(conv_id, user["id"])
    return conv


@router.get("/{conv_id}/messages")
def get_messages(conv_id: str, user=Depends(get_current_user)):
    conv = get_conversation(conv_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return list_messages(conv_id, user["id"])


@router.patch("/{conv_id}")
def rename(conv_id: str, data: ConvRename, user=Depends(get_current_user)):
    conv = get_conversation(conv_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    update_conv_meta(conv_id, user["id"], title=data.title,
                     folder=data.folder, pinned=data.pinned)
    return {"status": "success"}


@router.get("/{conv_id}/export")
def export_conv(conv_id: str, fmt: str = "md",
                user=Depends(get_current_user)):
    conv = get_conversation(conv_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = list_messages(conv_id, user["id"])
    from urllib.parse import quote as _q
    safe = _q(conv["title"])[:60] or "chat"
    if fmt == "json":
        import json as _json
        body = _json.dumps({"title": conv["title"], "messages": msgs},
                           ensure_ascii=False, indent=1)
        return Response(content=body, media_type="application/json",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{safe}.json"'})
    lines = [f"# {conv['title']}", "", f"_Exported from Aether — {conv['created_at']}_", ""]
    for m in msgs:
        who = "🧑 You" if m["role"] == "user" else "✨ Aether"
        lines += [f"**{who}** — {m['created_at']}", "", m["content"], ""]
    return Response(content="\n".join(lines), media_type="text/markdown",
                    headers={"Content-Disposition":
                             f'attachment; filename="{safe}.md"'})


@router.delete("/{conv_id}")
def delete(conv_id: str, user=Depends(get_current_user)):
    if delete_conversation(conv_id, user["id"]) == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "success"}
