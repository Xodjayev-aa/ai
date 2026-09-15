from __future__ import annotations

import asyncio
import io
import logging
import re

log = logging.getLogger("aether.speech")

_CODE = re.compile(r"```[\s\S]*?```")
_INLINE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)]\([^)]*\)")
_IMG = re.compile(r"!\[[^\]]*]\([^)]*\)")
_HEAD = re.compile(r"^#{1,6}\s+", re.M)
_QUOTE = re.compile(r"^\s*>\s+", re.M)
_BULLET = re.compile(r"^\s*[-*+]\s+", re.M)
_NUM = re.compile(r"^\s*\d+\.\s+", re.M)
_BOLD = re.compile(r"(\*\*|__)(.*?)\1")
_ITAL = re.compile(r"(\*|_)(.*?)\1")
_URL = re.compile(r"https?://\S+")
_MULTI_NL = re.compile(r"\n{3,}")
_SENT = re.compile(r"(?<=[.!?])\s+")

_ABBR = [
    (re.compile(r"\be\.g\.", re.I), "for example"),
    (re.compile(r"\bi\.e\.", re.I), "that is"),
    (re.compile(r"\bvs\.\b", re.I), "versus"),
    (re.compile(r"\bOK\b"), "okay"),
    (re.compile(r"\bAPI\b"), "A P I"),
    (re.compile(r"\bURL\b"), "U R L"),
    (re.compile(r"\bHTML\b"), "H T M L"),
    (re.compile(r"\bCSS\b"), "C S S"),
    (re.compile(r"\bJSON\b"), "jay-son"),
    (re.compile(r"\bSQL\b"), "S Q L"),
]


def for_ear(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = _CODE.sub(" Code snippet. ", t)
    t = _INLINE.sub(r"\1", t)
    t = _IMG.sub("", t)
    t = _LINK.sub(r"\1", t)
    t = _HEAD.sub("", t)
    t = _QUOTE.sub("", t)
    t = _BULLET.sub("", t)
    t = _NUM.sub("", t)
    t = _BOLD.sub(r"\2", t)
    t = _ITAL.sub(r"\2", t)
    t = _URL.sub("", t)
    for pat, repl in _ABBR:
        t = pat.sub(repl, t)
    t = _MULTI_NL.sub("\n\n", t)
    parts = [p.strip() for p in _SENT.split(t) if p.strip()]
    # Blank lines become breaths in neural TTS.
    spoken = "\n\n".join(parts)
    return spoken[:1800]


def _mood(text: str) -> tuple[str, str]:
    low = (text or "").lower()
    if "```" in (text or "") or re.search(r"\b(error|failed|warning|cannot|can't)\b", low):
        return "-10%", "-4Hz"
    if "?" in (text or "") and len(text) < 240:
        return "-4%", "+3Hz"
    if len(text or "") > 420:
        return "-8%", "-1Hz"
    return "-6%", "-2Hz"


async def synthesize(text: str) -> bytes | None:
    spoken = for_ear(text)
    if not spoken:
        return None
    rate, pitch = _mood(text)
    try:
        import edge_tts  # type: ignore

        buf = io.BytesIO()
        comm = edge_tts.Communicate(
            spoken,
            voice="en-US-JennyNeural",
            rate=rate,
            pitch=pitch,
            volume="-8%",
        )

        async def _run() -> None:
            async for chunk in comm.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    buf.write(chunk["data"])

        await asyncio.wait_for(_run(), timeout=25)
        data = buf.getvalue()
        if len(data) > 800:
            return data
    except Exception:
        log.warning("neural tts unavailable", exc_info=True)

    try:
        from gtts import gTTS  # type: ignore

        buf = io.BytesIO()
        await asyncio.to_thread(lambda: gTTS(spoken[:1000], lang="en", slow=False).write_to_fp(buf))
        data = buf.getvalue()
        if data:
            return data
    except Exception:
        log.warning("gtts unavailable", exc_info=True)
    return None
