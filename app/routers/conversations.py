import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import (
    create_conversation, delete_conversation, get_conversation,
    list_conversations, list_messages, rename_conversation,
)
from app.deps import get_current_user

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConvCreate(BaseModel):
    title: str = Field(default="New chat", max_length=120)


class ConvRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


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
    rename_conversation(conv_id, user["id"], data.title)
    return {"status": "success", "title": data.title}


@router.delete("/{conv_id}")
def delete(conv_id: str, user=Depends(get_current_user)):
    if delete_conversation(conv_id, user["id"]) == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "success"}
