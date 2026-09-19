"""Teach mode — the user teaches Aether how to answer *them*.

Two flows, both keyless and both ending in long-term memory the user can edit
or delete in Settings:

* ``POST /api/teach/start``   → a short, friendly interview (5 questions).
  ``POST /api/teach/answer``  → saves each answer as a memory right away, so
  nothing is lost if the user stops halfway.
  ``POST /api/teach/finish``  → optional one-call tidy-up of the wording.

* ``POST /api/teach/improve`` → the "Teach" button under any answer: "next time
  do it like this". Saves the correction as a durable instruction.

Every memory is a plain row in the existing memories table — visible, editable
and deletable in Settings, never a hidden profile.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.ai import client
from app.database import add_memory, list_memories, update_memory
from app.deps import get_current_user

router = APIRouter(prefix="/api/teach", tags=["teach"])

INTERVIEW = [
    {
        "id": "name",
        "prompt": "What should I call you?",
        "hint": "A first name or nickname is plenty.",
        "template": "The user goes by {answer}.",
    },
    {
        "id": "focus",
        "prompt": "What are you usually working on, studying, or curious about?",
        "hint": "School, projects, hobbies — anything I should keep in mind.",
        "template": "The user's main interests and work: {answer}",
    },
    {
        "id": "style",
        "prompt": "How do you like my answers?",
        "hint": "For example: short and direct, or detailed with examples.",
        "template": "Preferred answer style: {answer}",
    },
    {
        "id": "rules",
        "prompt": "Is there anything I should always do — or never do — when answering you?",
        "hint": "For example: always show the steps, never use jargon.",
        "template": "Always/never when answering this user: {answer}",
    },
    {
        "id": "careful",
        "prompt": "Any topics I should treat extra carefully?",
        "hint": "For example: exam prep, health, personal projects.",
        "template": "Topics to treat extra carefully: {answer}",
    },
]

_QUESTION_BY_ID = {q["id"]: q for q in INTERVIEW}
_SESSIONS: dict[str, dict] = {}   # tiny in-memory session store (best effort)
_MAX_ANSWER = 400


class AnswerBody(BaseModel):
    session_id: str = Field(default="", max_length=64)
    question_id: str = Field(min_length=1, max_length=32)
    answer: str = Field(min_length=1, max_length=_MAX_ANSWER)


class FinishBody(BaseModel):
    session_id: str = Field(default="", max_length=64)


class ImproveBody(BaseModel):
    instruction: str = Field(min_length=2, max_length=500)
    question: str | None = Field(default=None, max_length=500)


@router.post("/start")
def start(user=Depends(get_current_user)):
    session_id = uuid.uuid4().hex
    _SESSIONS[session_id] = {"user_id": user["id"], "answers": {}}
    if len(_SESSIONS) > 500:  # keep the map tiny on long-lived instances
        for key in list(_SESSIONS)[:100]:
            _SESSIONS.pop(key, None)
    return {
        "session_id": session_id,
        "questions": [{k: q[k] for k in ("id", "prompt", "hint")} for q in INTERVIEW],
        "note": "Answers are saved as memories you can edit or delete in Settings.",
    }


@router.post("/answer")
def answer(body: AnswerBody, user=Depends(get_current_user)):
    question = _QUESTION_BY_ID.get(body.question_id)
    if not question:
        raise HTTPException(status_code=400, detail="Unknown question")
    text = " ".join(body.answer.split())[:_MAX_ANSWER]
    memory_text = question["template"].format(answer=text)[:300]
    session = _SESSIONS.get(body.session_id)
    if session is not None and session.get("user_id") == user["id"]:
        session["answers"][body.question_id] = memory_text
    add_memory(user["id"], memory_text)
    return {"status": "saved", "memory": memory_text,
            "saved_count": len(list_memories(user["id"]))}


@router.post("/finish")
async def finish(body: FinishBody, user=Depends(get_current_user)):
    """Optional polish: ask the AI to phrase the fresh memories nicely.

    Best effort — if the free tier is busy the raw memories stay as they are.
    """
    session = _SESSIONS.get(body.session_id)
    answers = list((session or {}).get("answers", {}).items())
    _SESSIONS.pop(body.session_id, None)
    memories = list_memories(user["id"], limit=50)
    refined = 0
    if answers:
        facts = "\n".join(f"- {text}" for _, text in answers)
        try:
            raw = await client.complete(
                [{"role": "system", "content":
                    "Rewrite each line as one short, natural memory sentence a "
                    "friendly assistant would keep about its user. Keep every "
                    "fact, drop filler, no commentary. Reply with one line per "
                    "fact, in the same order, prefixed with '-'. Max 200 chars "
                    "per line."},
                 {"role": "user", "content": facts}],
                temperature=0.3, max_tokens=400,
            )
            lines = [re.sub(r"^[-•*\d.\s]+", "", ln).strip()
                     for ln in raw.splitlines() if ln.strip()]
            lines = [ln for ln in lines if 10 <= len(ln) <= 300][:len(answers)]
            if lines:
                for (question_id, old_text), new_text in zip(answers, lines):
                    row = next((m for m in memories
                                if m["content"].strip() == old_text.strip()), None)
                    if row and update_memory(row["id"], user["id"], new_text):
                        refined += 1
        except Exception:  # noqa: BLE001 — polish is optional
            refined = 0
    return {
        "status": "ok",
        "answered": len(answers),
        "refined": refined,
        "memories": list_memories(user["id"], limit=20),
    }


@router.post("/improve")
def improve(body: ImproveBody, user=Depends(get_current_user)):
    """The "Teach" button under an answer: remember a correction, forever."""
    instruction = " ".join(body.instruction.split())[:500]
    if body.question:
        question = " ".join(body.question.split())[:200]
        memory = f'When answering about "{question}", the user wants: {instruction}'
    else:
        memory = f"When answering this user: {instruction}"
    add_memory(user["id"], memory[:300])
    return {"status": "saved", "memory": memory[:300],
            "total": len(list_memories(user["id"]))}
