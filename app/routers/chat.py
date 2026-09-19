import json
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai import client, pipeline
from app.ai import config as ai_config
from app.database import (
    add_message, bump_usage, get_conversation, get_custom_instructions,
    get_doc, get_persona, get_usage, list_messages, list_memories,
    get_auto_memory, add_memory,
)
from app.deps import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])

_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_MAX_IMAGE_B64 = int(5.5 * 1024 * 1024 / 4 * 3)  # ~5.5MB base64


class ChatPayload(BaseModel):
    conversation_id: str
    message: str = Field(min_length=1, max_length=ai_config.MAX_MESSAGE_CHARS)
    force_polish: bool = False
    fast: bool = False
    mode: str = "ai"          # "ai" | "research"
    persona_id: str | None = None
    reasoning: bool = False
    image_b64: str | None = None
    doc_id: str | None = None


def _build_context_prefix(user, persona_id: str | None) -> tuple[str, bool]:
    """Custom instructions + long-term memory + persona -> system prefix."""
    parts, used_memory = [], False
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
    return "\n\n".join(parts), used_memory


def _augment_with_doc(message: str, doc_id: str | None) -> str:
    if not doc_id:
        return message
    doc = get_doc(doc_id, None) or {}
    return message


def _image_content(message: str, image_b64: str | None):
    """OpenAI vision content format (Gemini understands it too)."""
    if not image_b64 or not image_b64.startswith("data:image/"):
        return message
    if len(image_b64) > _MAX_IMAGE_B64:
        raise HTTPException(status_code=413, detail="Image too large (max ~4 MB)")
    return [{"type": "text", "text": message},
            {"type": "image_url", "image_url": {"url": image_b64}}]


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

    add_message(data.conversation_id, user["id"], "user", data.message,
                {"image": bool(data.image_b64), "doc": bool(data.doc_id)})
    bump_usage(user["id"])

    prefix, _ = _build_context_prefix(user, data.persona_id)

    async def sse():
        def event(payload: dict) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        # ── Research mode (keyless, no AI) — or URL reading ──
        if data.mode == "research":
            from app.ai import research as research_mod
            yield event({"type": "phase", "phase": "research"})
            url_match = _URL_RE.search(data.message)
            try:
                if url_match:
                    article = await research_mod.extract_article(url_match.group(0))
                    text = (f"## {article['title']}\n\n*Source: {article['url']}*\n\n"
                            + article["text"])
                    meta = {"mode": "research", "url": article["url"]}
                else:
                    result = await research_mod.research(data.message[:300])
                    text = result.answer_md
                    meta = {"mode": "research",
                            "sources": [s.url for s in result.sources][:8]}
            except Exception as exc:  # noqa: BLE001
                yield event({"type": "error",
                             "detail": f"Research sources unreachable: {exc}"})
                return
            for i in range(0, len(text), 220):
                yield event({"type": "delta", "text": text[i:i + 220]})
            yield event({"type": "final", "text": text, "polished": False,
                         "mode": "research"})
            saved = add_message(data.conversation_id, user["id"], "assistant",
                                text, meta)
            yield event({"type": "done", "message_id": saved["id"],
                         "polished": False, "mode": "research"})
            yield "data: [DONE]\n\n"
            return

        # ── AI mode ──
        question = data.message
        doc_note = ""
        if data.doc_id:
            doc = get_doc(data.doc_id, user["id"])
            if doc:
                excerpt = doc["text"][:9000]
                doc_note = f" (📎 {doc['name']})"
                question = (f"Attached document \"{doc['name']}\":\n\n{excerpt}\n\n"
                            f"User question about it: {data.message}")
        if _URL_RE.search(question) and not data.image_b64:
            from app.ai import research as research_mod
            url = _URL_RE.search(question).group(0)
            try:
                article = await research_mod.extract_article(url)
                question += (f"\n\n[Content of {url} — \"{article['title']}\"]:\n"
                             + article["text"][:6000])
            except Exception:
                pass  # link unreadable — the AI will say so if it matters

        final_text, polished = "", False
        try:
            content = _image_content(question, data.image_b64)
            async for ev in pipeline.smart_answer(
                data.message, history,
                force_polish=data.force_polish,
                fast=data.fast,
                system_prefix=prefix,
                reasoning=data.reasoning,
                user_content=content,
            ):
                if ev["type"] == "final":
                    final_text, polished = ev["text"], ev["polished"]
                yield event(ev)
        except client.AIUnavailableError as exc:
            yield event({"type": "error", "detail": str(exc)})
            return
        except HTTPException as exc:
            yield event({"type": "error", "detail": str(exc.detail)})
            return
        except Exception as exc:  # keep the stream honest
            yield event({"type": "error", "detail": f"Unexpected error: {exc}"})
            return

        if final_text:
            meta = {"polished": polished}
            if data.reasoning:
                meta["reasoned"] = True
            if data.image_b64:
                meta["image"] = True
            if doc_note:
                meta["doc"] = doc_note.strip()
            saved = add_message(data.conversation_id, user["id"], "assistant",
                                final_text, meta)
            # ── long-term memory extraction (best-effort, off by toggle) ──
            try:
                if get_auto_memory(user["id"]) and len(final_text) > 40:
                    facts = await pipeline.extract_facts(data.message, final_text)
                    for f in facts:
                        add_memory(user["id"], f)
            except Exception:
                pass
            yield event({"type": "done", "message_id": saved["id"],
                         "polished": polished, "title": conv["title"]})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
