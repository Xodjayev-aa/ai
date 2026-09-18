"""Smart two-stage answering: draft with one AI, polish with another —
but only when the question actually deserves it (saves free-tier quota)."""

import re
import typing

from app.ai import client

KEYLESS_KEY = "pollinations-anon"

SYSTEM_PROMPT = (
    "You are Aether, a helpful, accurate and friendly AI assistant. "
    "Answer in clear markdown (## headings, **bold**, bullet lists, code "
    "fences with language tags). Be direct and complete — no filler, no "
    "unnecessary apologies. If asked for facts you are unsure about, say so. "
    "Match the language of the user's message."
)

EDITOR_PROMPT = (
    "You are a meticulous senior editor. Below is a draft answer produced by "
    "another AI. Improve it and return ONLY the improved answer:\n"
    "- Fix factual, logical and math errors\n"
    "- Sharpen clarity and flow; remove repetition and filler\n"
    "- Keep (or improve) markdown structure and code correctness\n"
    "- Preserve all genuinely useful details; keep the same language\n"
    "- Do not add commentary about editing — just output the final answer"
)

POLISH_TRIGGERS = re.compile(
    r"\b(explain|why|how (?:do|does|did|can|could|would)|compare|pros and cons|"
    r"step[- ]by[- ]step|essay|report|article|story|detailed|in depth|in-depth|"
    r"elaborate|analyze|analyse|evaluate|design|plan|strategy|architecture|"
    r"presentation|slides|deck|research|summarize|summarise|review|tutorial|"
    r"guide|best way|optimize|improve)\b",
    re.IGNORECASE,
)


def should_polish(question: str, draft: str, *, force: bool = False) -> bool:
    """Heuristic: only burn a second API call when it's worth it."""
    if force:
        return True
    q = question.strip()
    d = draft.strip()
    if not d:
        return False
    if len(d) > 1500:                     # long answers benefit most
        return True
    if POLISH_TRIGGERS.search(q):
        return True
    if q.count("?") > 1 or len(q.split()) > 45:  # multi-part questions
        return True
    if "```" in d and len(d) > 400:       # code-heavy answers
        return True
    return False


async def draft_answer(
    question: str,
    history: list[dict],
    *,
    fast: bool = False,
) -> tuple[str, str]:
    """Stage 1. Returns (text, provider_key_used_hint is implicit)."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})
    text = await client.complete(messages, fast=fast)
    return text


async def polish_answer(question: str, draft: str,
                        exclude_keys: set[str]) -> str:
    """Stage 2. Runs on a *different* provider key than the draft used."""
    messages = [
        {"role": "system", "content": EDITOR_PROMPT},
        {"role": "user", "content":
            f"QUESTION:\n{question}\n\nDRAFT ANSWER:\n{draft}"},
    ]
    chunks: list[str] = []
    # The keyless fallback has only one "key" — don't let the exclusion
    # rule block polish when it's the only provider left.
    async for delta in client.stream_chat(
            messages, temperature=0.4,
            exclude_keys={k for k in exclude_keys if k != KEYLESS_KEY}):
        chunks.append(delta)
    return "".join(chunks).strip() or draft


async def smart_answer(
    question: str,
    history: list[dict],
    *,
    force_polish: bool = False,
    fast: bool = False,
) -> typing.AsyncIterator[dict]:
    """Yields SSE-style events:
       {"type": "phase", "phase": "draft"}
       {"type": "delta", "text": "..."}
       {"type": "phase", "phase": "polish"}
       {"type": "final", "text": ..., "polished": bool}
    """
    # ---- Stage 1: draft (streamed live to the user)
    yield {"type": "phase", "phase": "draft"}
    draft_chunks: list[str] = []
    try:
        async for delta in client.stream_chat(
            [{"role": "system", "content": SYSTEM_PROMPT}, *history,
             {"role": "user", "content": question}],
            fast=fast,
        ):
            draft_chunks.append(delta)
            yield {"type": "delta", "text": delta}
    except client.AIUnavailableError as exc:
        yield {"type": "error", "detail": str(exc)}
        return
    draft = "".join(draft_chunks)

    # ---- Stage 2: polish (only when it matters)
    if should_polish(question, draft, force=force_polish):
        yield {"type": "phase", "phase": "polish"}
        try:
            final = await polish_answer(question, draft, exclude_keys=set())
            if final.strip():
                yield {"type": "final", "text": final, "polished": True}
                return
        except client.AIUnavailableError:
            # Polish provider unavailable — the draft is already good.
            pass
    yield {"type": "final", "text": draft, "polished": False}
