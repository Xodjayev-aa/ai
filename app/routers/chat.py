"""Chat streaming: SSE with keep-alive, honest queueing and stop-safe saves.

Design notes (why it looks like this):

* **Nothing dies silently.** Every failure becomes an ``error`` event with a
  ``retryable`` flag and, when the free tier is cooling down, a
  ``retry_after`` the UI counts down before auto-retrying.
* **Keep-alive.** Serverless platforms and proxies drop idle streams, and the
  keyless free tier can take a while, so we emit a ``ping`` (plus a real SSE
  comment) every few seconds while no tokens are flowing.
* **Stop keeps the partial answer.** The browser generates the assistant
  message id; the server upserts the finished answer under that id and
  ``/api/chat/partial`` upserts whatever had streamed so far. A shorter partial
  never overwrites a longer finished answer, so stopping is idempotent.
* **Regenerate** re-answers the last user message without duplicating it.
"""

import asyncio
import json
import re
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai import client, keyless, pipeline
from app.ai import config as ai_config
from app.database import (
    add_memory, add_message, bump_usage, get_auto_memory, get_conversation,
    get_custom_instructions, get_doc, get_persona, get_usage, list_memories,
    list_messages, upsert_message,
)
from app.deps import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])

_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_MAX_IMAGE_B64 = int(5.5 * 1024 * 1024 / 4 * 3)  # ~5.5MB base64
KEEPALIVE_SECONDS = 9.0


class ChatPayload(BaseModel):
    conversation_id: str
    message: str = Field(default="", max_length=ai_config.MAX_MESSAGE_CHARS)
    regenerate: bool = False
    assistant_message_id: str | None = None
    client_saves_partial: bool = True
    force_polish: bool = False
    fast: bool = False
    mode: str = "ai"          # "ai" | "research"
    persona_id: str | None = None
    reasoning: bool = False
    image_b64: str | None = None
    doc_id: str | None = None
    voice: bool = False
    teach_instruction: str | None = Field(default=None, max_length=500)


class PartialPayload(BaseModel):
    conversation_id: str
    message_id: str
    content: str = Field(default="", max_length=ai_config.MAX_MESSAGE_CHARS * 2)


def _quota(user) -> dict:
    used = get_usage(user["id"])
    limit = ai_config.DAILY_REQUEST_LIMIT
    return {"used_today": used, "limit": limit, "remaining": max(0, limit - used)}


def _build_context_prefix(user, persona_id: str | None,
                          teach_instruction: str | None = None,
                          voice: bool = False) -> tuple[str, bool]:
    """Custom instructions + long-term memory + persona + preferences -> prefix.

    Preferences contribute the language clause, the language-style clause and
    (in voice mode) the exam-examiner persona, so chat and voice stay in sync.
    """
    parts, used_memory = [], False
    from app.preferences import prompt_clauses
    prefs = get_prefs(user["id"])
    clauses = prompt_clauses(prefs, voice=voice)
    if clauses:
        parts.append(clauses)
    instr = get_custom_instructions(user["id"])
    if instr:
        parts.append(f"The user's standing instructions (always apply):\n{instr}")
    if get_auto_memory(user["id"]):
        mems = list_memories(user["id"], limit=20)
        if mems:
            used_memory = True
            lines = "\n".join(f"- {m['content']}" for m in mems)
            parts.append(f"Things you remember about this user "
                         f"(naturally use if relevant, never recite):\n{lines}")
    if persona_id:
        persona = get_persona(persona_id, user["id"])
        if persona:
            parts.insert(0, f"PERSONA — you are \"{persona['name']}\". "
                            f"{persona['prompt']}")
    if teach_instruction:
        parts.append(
            "The user just corrected you. Apply this from now on, in this "
            f"answer and in future ones:\n{teach_instruction.strip()}"
        )
    return "\n\n".join(parts), used_memory


def _image_content(message: str, image_b64: str | None):
    """OpenAI vision content format (Gemini understands it too)."""
    if not image_b64 or not image_b64.startswith("data:image/"):
        return message
    if len(image_b64) > _MAX_IMAGE_B64:
        raise HTTPException(status_code=413, detail="Image too large (max ~4 MB)")
    return [{"type": "text", "text": message},
            {"type": "image_url", "image_url": {"url": image_b64}}]


def _history_for(data: ChatPayload, user) -> list[dict]:
    history = list_messages(data.conversation_id, user["id"],
                            limit=ai_config.MAX_HISTORY_MESSAGES)
    history = [{"role": m["role"], "content": m["content"]}
               for m in history if m["role"] in ("user", "assistant")]
    if data.regenerate and history and history[-1]["role"] == "assistant":
        history = history[:-1]  # re-answer the last question
    return history


@router.post("/stream")
async def stream_chat(data: ChatPayload, request: Request,
                      user=Depends(get_current_user)):
    conv = get_conversation(data.conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    quota = _quota(user)
    if quota["remaining"] <= 0:
        raise HTTPException(
            status_code=429,
            detail=(f"Daily limit of {quota['limit']} AI requests reached. "
                    "It resets at midnight UTC."),
            headers={"X-Usage-Used": str(quota["used_today"]),
                     "X-Usage-Limit": str(quota["limit"])},
        )
    if not data.regenerate and not data.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")

    history = _history_for(data, user)
    prefix, _ = _build_context_prefix(user, data.persona_id, data.teach_instruction,
                                      voice=bool(data.voice))

    # The assistant row exists from the start so a stopped answer is still a
    # real, resumable message in the conversation.
    assistant_id = data.assistant_message_id or str(uuid.uuid4())
    queued_wait = round(keyless.pacer.estimated_wait(), 1)

    if data.mode == "research":
        # No user row for regenerate in research mode; keep the original rule.
        if not data.regenerate:
            add_message(data.conversation_id, user["id"], "user", data.message,
                        {"image": bool(data.image_b64), "doc": bool(data.doc_id)})
        bump_usage(user["id"])
        return StreamingResponse(
            _research_stream(data, user, conv, assistant_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "Connection": "keep-alive"},
        )

    if not data.regenerate:
        add_message(data.conversation_id, user["id"], "user", data.message,
                    {"image": bool(data.image_b64), "doc": bool(data.doc_id)})
    bump_usage(user["id"])

    return StreamingResponse(
        _ai_stream(data, user, conv, request, history, prefix, assistant_id,
                   queued_wait, quota),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _research_stream(data: ChatPayload, user, conv, assistant_id: str):
    """Keyless research mode: Wikipedia + DuckDuckGo, no AI at all."""
    from app.ai import research as research_mod

    yield _event({"type": "meta", "conversation_id": data.conversation_id,
                  "message_id": assistant_id, "mode": "research",
                  "usage": _quota(user)})
    yield _event({"type": "phase", "phase": "research",
                  "detail": "Searching Wikipedia & DuckDuckGo"})
    url_match = _URL_RE.search(data.message)
    try:
        if url_match:
            article = await research_mod.extract_article(url_match.group(0))
            text = (f"## {article['title']}\n\n*Source: {article['url']}*\n\n"
                    + article["text"])
            meta = {"mode": "research", "url": article["url"], "live": True}
        else:
            result = await research_mod.research(data.message[:300])
            text = result.answer_md
            meta = {"mode": "research",
                    "sources": [s.url for s in result.sources][:8], "live": True}
    except Exception as exc:  # noqa: BLE001
        yield _event({"type": "error", "retryable": True,
                      "detail": f"Research sources unreachable: {exc}"})
        yield "data: [DONE]\n\n"
        return
    for i in range(0, len(text), 220):
        yield _event({"type": "delta", "text": text[i:i + 220]})
    yield _event({"type": "final", "text": text, "polished": False,
                  "mode": "research"})
    upsert_message(assistant_id, data.conversation_id, user["id"], "assistant",
                   text, meta)
    yield _event({"type": "done", "message_id": assistant_id,
                  "conversation_id": data.conversation_id,
                  "mode": "research", "title": conv["title"],
                  "usage": _quota(user)})
    yield "data: [DONE]\n\n"


async def _ai_stream(data: ChatPayload, user, conv, request: Request,
                     history: list[dict], prefix: str, assistant_id: str,
                     queued_wait: float, quota: dict):
    """AI mode: stream the answer with keep-alives and stop-safe persistence."""
    buffer: list[str] = []
    polished = False
    final_text = ""
    error: dict | None = None
    started = time.time()

    yield _event({"type": "meta", "conversation_id": data.conversation_id,
                  "message_id": assistant_id, "voice": data.voice,
                  "usage": quota,
                  "model_note": "free shared AI — short waits possible"})
    if queued_wait > 1.5:
        yield _event({"type": "phase", "phase": "queued",
                      "wait_seconds": queued_wait,
                      "detail": f"Free AI is shared — about {int(round(queued_wait))}s "
                                "in the queue"})

    queue: asyncio.Queue = asyncio.Queue()

    async def produce() -> None:
        try:
            async for ev in _produce_events(data, user, history, prefix):
                await queue.put(ev)
        except Exception as exc:  # noqa: BLE001 — never leak a stack trace
            await queue.put({"type": "error", "retryable": True,
                             "detail": f"Unexpected error: {exc}"})
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    stopped = False
    try:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                # No tokens for a while: check the client and say something.
                if await request.is_disconnected():
                    stopped = True
                    break
                yield ": keep-alive\n\n"
                yield _event({"type": "ping",
                              "elapsed": round(time.time() - started, 1),
                              "text": "".join(buffer)})
                continue
            if ev is None:
                break
            if ev["type"] == "delta":
                buffer.append(ev["text"])
            elif ev["type"] == "final":
                final_text, polished = ev["text"], bool(ev.get("polished"))
            elif ev["type"] == "error":
                error = ev
            yield _event(ev)
            if ev["type"] == "error":
                break
    finally:
        if stopped or task.done() is False:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    text = final_text or "".join(buffer)
    if error and not text:
        # Nothing usable: the user message stays, no empty assistant bubble.
        yield _event({"type": "error", **error})
        yield "data: [DONE]\n\n"
        return

    meta = {"polished": polished, "voice": True} if data.voice else {"polished": polished}
    if data.reasoning:
        meta["reasoned"] = True
    if data.image_b64:
        meta["image"] = True
    if data.doc_id:
        meta["doc"] = True
    if stopped:
        meta["stopped"] = True
    if error:
        meta["error"] = str(error.get("detail", ""))[:200]

    upsert_message(assistant_id, data.conversation_id, user["id"], "assistant",
                   text, meta)

    if stopped and not data.client_saves_partial:
        # The browser vanished mid-answer: whatever streamed is the answer.
        pass

    if not stopped:
        # Long-term memory extraction (best-effort, off by toggle).
        try:
            if get_auto_memory(user["id"]) and len(text) > 40 and not data.voice:
                facts = await pipeline.extract_facts(data.message, text)
                for fact in facts:
                    add_memory(user["id"], fact)
        except Exception:  # noqa: BLE001
            pass
        yield _event({"type": "done", "message_id": assistant_id,
                      "conversation_id": data.conversation_id,
                      "polished": polished, "title": conv["title"],
                      "usage": _quota(user), "stopped": False})
    else:
        yield _event({"type": "done", "message_id": assistant_id,
                      "conversation_id": data.conversation_id,
                      "polished": polished, "title": conv["title"],
                      "usage": _quota(user), "stopped": True})
    yield "data: [DONE]\n\n"


async def _produce_events(data: ChatPayload, user, history: list[dict], prefix: str):
    """Question preparation + the two-stage answer, as plain events."""
    question = data.message
    if data.doc_id:
        doc = get_doc(data.doc_id, user["id"])
        if doc:
            excerpt = doc["text"][:9000]
            question = (f"Attached document \"{doc['name']}\":\n\n{excerpt}\n\n"
                        f"User question about it: {data.message}")
    elif _URL_RE.search(question) and not data.image_b64:
        from app.ai import research as research_mod

        url = _URL_RE.search(question).group(0)
        try:
            article = await research_mod.extract_article(url)
            question += (f"\n\n[Content of {url} — \"{article['title']}\"]:\n"
                         + article["text"][:6000])
        except Exception:  # noqa: BLE001
            pass  # link unreadable — the AI will say so if it matters

    try:
        content = _image_content(question, data.image_b64)
        async for ev in pipeline.smart_answer(
            question, history,
            force_polish=data.force_polish,
            fast=data.fast,
            system_prefix=prefix,
            reasoning=data.reasoning,
            user_content=content,
            voice=data.voice,
        ):
            yield ev
    except client.AIUnavailableError as exc:
        yield {"type": "error", "detail": str(exc), "retryable": True,
               "retry_after": round(max(keyless.pacer.cooldown_remaining(), 8), 1)}
    except keyless.KeylessError as exc:
        yield {"type": "error", "detail": str(exc), "retryable": exc.retryable,
               "retry_after": round(exc.retry_after or
                                    keyless.pacer.cooldown_remaining() or 8, 1)}
    except HTTPException as exc:
        yield {"type": "error", "detail": str(exc.detail), "retryable": False}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "detail": f"Unexpected error: {exc}",
               "retryable": True}


@router.post("/partial")
def save_partial(data: PartialPayload, user=Depends(get_current_user)):
    """Persist a stopped answer. Shorter partials never overwrite longer ones."""
    conv = get_conversation(data.conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    text = (data.content or "").strip()
    if not text:
        return {"status": "empty"}
    saved = upsert_message(data.message_id, data.conversation_id, user["id"],
                           "assistant", text, {"polished": False, "stopped": True})
    return {"status": "saved", "message_id": saved["id"]}
