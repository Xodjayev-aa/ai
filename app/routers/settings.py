"""User settings: custom instructions, long-term memory, personas, export."""

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.database import (
    create_persona, delete_memory, delete_persona, get_auto_memory,
    get_custom_instructions, list_conversations, list_memories, list_messages,
    list_personas, set_auto_memory, set_custom_instructions,
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
    convs = list_conversations(user["id"], limit=500)
    data = []
    for c in convs:
        data.append({"title": c["title"], "created_at": c["created_at"],
                     "messages": list_messages(c["id"], user["id"])})
    payload = json.dumps({"account": user["email"], "conversations": data},
                         ensure_ascii=False, indent=1)
    return Response(content=payload, media_type="application/json",
                    headers={"Content-Disposition":
                             'attachment; filename="aether-export.json"'})
