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

REASONING_PROMPT = (
    "Before answering, reason through the problem privately and carefully: "
    "consider alternative interpretations, verify your logic, check for "
    "errors, then present only the polished final answer. Prefer accuracy "
    "over speed. If uncertain, say precisely what is uncertain and why."
)

FACT_EXTRACT_PROMPT = (
    "Extract durable facts about the user (preferences, identity, ongoing "
    "projects, constraints) worth remembering for future conversations. "
    "Respond with ONLY a JSON array of short strings. Return [] if nothing "
    "worth remembering. Never store sensitive data (passwords, full names of "
    "third parties, secrets)."
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


def build_system_prompt(system_prefix: str = "",
                        reasoning: bool = False) -> str:
    parts = [SYSTEM_PROMPT]
    if system_prefix:
        parts.append(system_prefix.strip())
    if reasoning:
        parts.append(REASONING_PROMPT)
    return "\n\n".join(p for p in parts if p)


async def extract_facts(question: str, answer: str) -> list[str]:
    """Ask a fast model for durable user facts. Best-effort, never raises."""
    try:
        raw = await client.complete(
            [{"role": "system", "content": FACT_EXTRACT_PROMPT},
             {"role": "user", "content":
                 f"USER MESSAGE:\n{question[:1000]}\n\nASSISTANT ANSWER:\n{answer[:1500]}"}],
            fast=True, temperature=0.0, max_tokens=200)
        import json as _json
        data = _json.loads(re.search(r"\[.*\]", raw, re.DOTALL).group(0))
        if isinstance(data, list):
            return [str(x).strip()[:300] for x in data
                    if isinstance(x, str) and x.strip()][:3]
    except Exception:
        pass
    return []


async def smart_answer(
    question: str,
    history: list[dict],
    *,
    force_polish: bool = False,
    fast: bool = False,
    system_prefix: str = "",
    reasoning: bool = False,
    user_content: str | list | None = None,
) -> typing.AsyncIterator[dict]:
    """`question` is plain text (used for heuristics/polish/memory);
    `user_content` optionally overrides the draft user message (e.g. for
    OpenAI-style vision content lists)."""
    """Yields SSE-style events:
       {"type": "phase", "phase": "draft"}
       {"type": "delta", "text": "..."}
       {"type": "phase", "phase": "polish"}
       {"type": "final", "text": ..., "polished": bool}
    """
    # ---- Stage 1: draft (streamed live to the user)
    yield {"type": "phase", "phase": "draft"}
    draft_chunks: list[str] = []
    used_keys: list[str] = []
    try:
        async for delta in client.stream_chat(
            [{"role": "system",
              "content": build_system_prompt(system_prefix, reasoning)},
             *history,
             {"role": "user", "content": user_content or question}],
            fast=fast and not reasoning,
            used=used_keys,
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
