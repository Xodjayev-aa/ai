"""User settings: custom instructions, long-term memory, personas, export."""

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.database import (
    add_memory, create_persona, delete_memory, delete_persona, get_auto_memory, get_prefs,
    set_prefs,
    get_custom_instructions, list_conversations, list_memories, list_messages,
    list_personas, set_auto_memory, set_custom_instructions, update_memory,
)
from app.deps import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])


# --------------------------------------------------- custom instructions

class InstructionsBody(BaseModel):
    text: str = Field(max_length=2000)


@router.get("/instructions")
def get_instructions(user=Depends(get_current_user)):
    return {"text": get_custom_instructions(user["id"])}


@router.put("/instructions")
def put_instructions(body: InstructionsBody, user=Depends(get_current_user)):
    set_custom_instructions(user["id"], body.text)
    return {"status": "success"}


# ------------------------------------------------------------ preferences

@router.get("/prefs")
def prefs_get(user=Depends(get_current_user)):
    """Voice / language / style / exam preferences (all defaulted)."""
    return get_prefs(user["id"])


@router.put("/prefs")
def prefs_put(body: dict, user=Depends(get_current_user)):
    """Partial update — send only the keys you change."""
    return set_prefs(user["id"], body or {})


# --------------------------------------------------------- auto memory

class AutoMemoryBody(BaseModel):
    enabled: bool


@router.get("/memory")
def memory_list(user=Depends(get_current_user)):
    return {"auto": get_auto_memory(user["id"]),
            "memories": list_memories(user["id"])}


@router.put("/memory/auto")
def memory_auto(body: AutoMemoryBody, user=Depends(get_current_user)):
    set_auto_memory(user["id"], body.enabled)
    return {"status": "success", "auto": body.enabled}


class MemoryBody(BaseModel):
    content: str = Field(min_length=2, max_length=300)


@router.post("/memory")
def memory_create(body: MemoryBody, user=Depends(get_current_user)):
    """Used by Teach mode and by the "add a memory" field in Settings."""
    add_memory(user["id"], body.content)
    return {"status": "saved", "memories": list_memories(user["id"])}


@router.patch("/memory/{memory_id}")
def memory_update(memory_id: str, body: MemoryBody,
                  user=Depends(get_current_user)):
    if update_memory(memory_id, user["id"], body.content) == 0:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "updated", "content": body.content.strip()[:300]}


@router.delete("/memory/{memory_id}")
def memory_delete(memory_id: str, user=Depends(get_current_user)):
    if delete_memory(memory_id, user["id"]) == 0:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted"}


# -------------------------------------------------------------- personas

class PersonaBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    prompt: str = Field(min_length=1, max_length=2000)


@router.get("/personas")
def personas_list(user=Depends(get_current_user)):
    return list_personas(user["id"])


@router.post("/personas")
def personas_create(body: PersonaBody, user=Depends(get_current_user)):
    if len(list_personas(user["id"])) >= 20:
        raise HTTPException(status_code=400, detail="Persona limit reached (20).")
    return create_persona(user["id"], body.name, body.prompt)


@router.delete("/personas/{persona_id}")
def personas_delete(persona_id: str, user=Depends(get_current_user)):
    if delete_persona(persona_id, user["id"]) == 0:
        raise HTTPException(status_code=404, detail="Persona not found")
    return {"status": "deleted"}


# ----------------------------------------------------------- export all

@router.get("/export")
def export_all(user=Depends(get_current_user)):
    """Everything the account holds: chats, personas, memories, settings."""
    convs = list_conversations(user["id"], limit=500)
    data = []
    for c in convs:
        data.append({"title": c["title"], "created_at": c["created_at"],
                     "folder": c.get("folder"), "pinned": bool(c.get("pinned")),
                     "messages": list_messages(c["id"], user["id"])})
    payload = json.dumps({
        "account": user["email"],
        "exported_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "settings": {
            "custom_instructions": get_custom_instructions(user["id"]),
            "preferences": get_prefs(user["id"]),
            "long_term_memory": get_auto_memory(user["id"]),
        },
        "memories": list_memories(user["id"]),
        "personas": list_personas(user["id"]),
        "conversations": data,
    }, ensure_ascii=False, indent=1)
    return Response(content=payload, media_type="application/json",
                    headers={"Content-Disposition":
                             'attachment; filename="aether-export.json"'})
