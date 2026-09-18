import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai import client, pipeline
from app.ai import config as ai_config
from app.database import add_message, bump_usage, get_conversation, get_usage, \
    list_messages
from app.deps import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatPayload(BaseModel):
    conversation_id: str
    message: str = Field(min_length=1, max_length=ai_config.MAX_MESSAGE_CHARS)
    force_polish: bool = False
    fast: bool = False
    mode: str = "ai"  # "ai" | "research"


@router.post("/stream")
async def stream_chat(data: ChatPayload, user=Depends(get_current_user)):
    conv = get_conversation(data.conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if get_usage(user["id"]) >= ai_config.DAILY_REQUEST_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit of {ai_config.DAILY_REQUEST_LIMIT} AI requests "
                   "reached. Resets at midnight UTC.",
        )

    history = list_messages(data.conversation_id, user["id"],
                            limit=ai_config.MAX_HISTORY_MESSAGES)
    history = [{"role": m["role"], "content": m["content"]}
               for m in history if m["role"] in ("user", "assistant")]

    # Persist the user's message before answering.
    add_message(data.conversation_id, user["id"], "user", data.message)
    bump_usage(user["id"])

    async def sse():
        def event(payload: dict) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        # ── Research mode: pure retrieval from Wikipedia/DDG, no AI ──
        if data.mode == "research":
            from app.ai import research as research_mod
            yield event({"type": "phase", "phase": "research"})
            try:
                result = await research_mod.research(data.message[:300])
            except Exception as exc:  # noqa: BLE001
                yield event({"type": "error",
                             "detail": f"Research sources unreachable: {exc}"})
                return
            text = result.answer_md
            for i in range(0, len(text), 220):
                yield event({"type": "delta", "text": text[i:i + 220]})
            yield event({"type": "final", "text": text, "polished": False,
                         "mode": "research"})
            saved = add_message(data.conversation_id, user["id"], "assistant",
                                text, {"mode": "research",
                                       "sources": [s.url for s in result.sources][:8]})
            yield event({"type": "done", "message_id": saved["id"],
                         "polished": False, "mode": "research"})
            yield "data: [DONE]\n\n"
            return

        # ── AI mode: provider chain (keyed -> keyless Pollinations) ──
        final_text, polished = "", False
        try:
            async for ev in pipeline.smart_answer(
                data.message, history,
                force_polish=data.force_polish, fast=data.fast,
            ):
                if ev["type"] == "final":
                    final_text, polished = ev["text"], ev["polished"]
                yield event(ev)
        except client.AIUnavailableError as exc:
            yield event({"type": "error", "detail": str(exc)})
            return
        except Exception as exc:  # keep the stream honest on unexpected errors
            yield event({"type": "error", "detail": f"Unexpected error: {exc}"})
            return
        if final_text:
            saved = add_message(data.conversation_id, user["id"], "assistant",
                                final_text, {"polished": polished})
            yield event({"type": "done", "message_id": saved["id"],
                         "polished": polished,
                         "title": conv["title"]})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
