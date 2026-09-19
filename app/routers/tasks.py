"""Scheduled tasks: a prompt that runs every day at a chosen hour (UTC),
its answer lands as a message in a dedicated conversation.

Vercel Cron hits GET /api/cron/daily once per day (see vercel.json).
Optional protection: set CRON_SECRET env var and Vercel sends it as
`Authorization: Bearer <secret>` header on cron calls.
"""

import datetime
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.ai import client
from app.database import (
    add_message, bump_usage, create_conversation, create_task, delete_task,
    get_user_by_id, list_tasks, mark_task_run, due_tasks,
)
from app.deps import get_current_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    hour_utc: int = Field(default=6, ge=0, le=23)


@router.post("")
def create(user=Depends(get_current_user), body: TaskBody = None):
    if len(list_tasks(user["id"])) >= 10:
        raise HTTPException(status_code=400, detail="Task limit reached (10).")
    conv = create_conversation(user["id"], f"Daily · {body.prompt[:52]}")
    task = create_task(user["id"], conv["id"], body.prompt, body.hour_utc)
    return task


@router.get("")
def index(user=Depends(get_current_user)):
    return list_tasks(user["id"])


@router.delete("/{task_id}")
def remove(task_id: str, user=Depends(get_current_user)):
    if delete_task(task_id, user["id"]) == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "deleted"}


cron_router = APIRouter(prefix="/api/cron", tags=["cron"])


@cron_router.get("/daily")
async def run_daily(authorization: str = Header(default="")):
    secret = os.getenv("CRON_SECRET", "")
    if secret and authorization != f"Bearer {secret}":
        raise HTTPException(status_code=403, detail="Bad cron secret")
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    hour = datetime.datetime.now(datetime.timezone.utc).hour
    results = {"ran": 0, "errors": 0}
    for task in due_tasks(hour, today):
        try:
            user = get_user_by_id(task["user_id"])
            if not user:
                continue
            answer = await client.complete(
                [{"role": "system",
                  "content": "You are Aether running a scheduled daily task. "
                             "Produce the requested output concisely in markdown."},
                 {"role": "user", "content": task["prompt"]}],
                max_tokens=900)
            add_message(task["conversation_id"], task["user_id"], "assistant",
                        answer, {"scheduled": task["prompt"][:120]})
            mark_task_run(task["id"], today)
            results["ran"] += 1
        except Exception:
            results["errors"] += 1
    return results
