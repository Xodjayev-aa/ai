from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.security import get_approved_user

router = APIRouter(prefix="/api/generate", tags=["media"])


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=1200)
    width: int = Field(default=1024, ge=256, le=1440)
    height: int = Field(default=1024, ge=256, le=1440)
    style: str = Field(default="")


class AudioRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/image")
async def generate_image(body: ImageRequest, request: Request, user: dict = Depends(get_approved_user)):
    prompt = body.prompt.strip()
    if body.style:
        prompt = f"{prompt}, {body.style.strip()} style"
    try:
        result = await request.app.state.client.generate_image(prompt, width=body.width, height=body.height)
    except Exception:
        raise HTTPException(status_code=502, detail="Image generation is busy. Try again.")
    url = result.get("url")
    if not url:
        raise HTTPException(status_code=502, detail="No image was returned.")
    return {"url": url, "model": result.get("model"), "prompt": prompt}


class DeckRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=400)


def _fallback_deck(topic: str) -> dict:
    t = topic.strip()[:120]
    return {
        "title": t,
        "slides": [
            {
                "type": "title",
                "title": t,
                "subtitle": "A briefing generated in Aether. Edit any line before you present.",
            },
            {
                "type": "features",
                "title": "What this covers",
                "subtitle": f"A structured pass over {t}",
                "items": [
                    f"Why {t} matters right now",
                    "The moving parts a decision-maker should see",
                    "Risks, constraints, and what to verify",
                    "A practical next step",
                ],
            },
            {
                "type": "compare",
                "title": "Tradeoffs",
                "subtitle": "Hold two views at once",
                "leftTitle": "Lean in",
                "rightTitle": "Watch outs",
                "left": [f"Clear upside if {t} is executed well", "Compounds with existing work"],
                "right": ["Unverified claims must be checked", "Scope can sprawl without an owner"],
            },
            {
                "type": "stats",
                "title": "Working snapshot",
                "subtitle": "Replace the figure with a sourced number before presenting",
                "statNumber": "—",
                "statLabel": f"Add a verified metric for {t}",
            },
        ],
    }


def _parse_deck(raw: str, topic: str) -> dict:
    import json
    import re

    text = (raw or "").strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return _fallback_deck(topic)
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return _fallback_deck(topic)
    slides = data.get("slides") if isinstance(data, dict) else None
    if not isinstance(slides, list) or not slides:
        return _fallback_deck(topic)
    clean = []
    for item in slides[:10]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "features")
        if kind not in {"title", "features", "stats", "compare"}:
            kind = "features"
        slide = {
            "type": kind,
            "title": str(item.get("title") or topic)[:160],
            "subtitle": str(item.get("subtitle") or "")[:240],
            "items": [str(x)[:200] for x in (item.get("items") or [])[:6] if str(x).strip()],
            "statNumber": str(item.get("statNumber") or "")[:24],
            "statLabel": str(item.get("statLabel") or "")[:180],
            "leftTitle": str(item.get("leftTitle") or "A")[:40],
            "rightTitle": str(item.get("rightTitle") or "B")[:40],
            "left": [str(x)[:200] for x in (item.get("left") or [])[:4] if str(x).strip()],
            "right": [str(x)[:200] for x in (item.get("right") or [])[:4] if str(x).strip()],
            "image": None,
        }
        clean.append(slide)
    if not clean:
        return _fallback_deck(topic)
    return {"title": str(data.get("title") or topic)[:160], "slides": clean}


@router.post("/deck")
async def generate_deck(body: DeckRequest, request: Request, user: dict = Depends(get_approved_user)):
    topic = body.topic.strip()
    messages = [
        {
            "role": "system",
            "content": (
                "You generate presentation decks. Reply with JSON only, no markdown. "
                'Shape: {"title": str, "slides": [{"type": "title"|"features"|"stats"|"compare", '
                '"title": str, "subtitle": str, "items": [str], "statNumber": str, "statLabel": str, '
                '"leftTitle": str, "rightTitle": str, "left": [str], "right": [str]}]}. '
                "4 to 6 slides. Be concrete. Never invent precise statistics — use '—' if unknown. "
                "Do not include images."
            ),
        },
        {"role": "user", "content": f"Create a short executive deck about: {topic}"},
    ]
    raw = ""
    try:
        from app.config import get_settings

        raw = await request.app.state.client.chat(
            messages, model=get_settings().model_chat, max_tokens=1400
        )
    except Exception:
        raw = ""
    deck = _parse_deck(raw, topic)
    return {"deck": deck, "source": "model" if raw else "template"}


@router.post("/audio")
async def generate_audio(body: AudioRequest, user: dict = Depends(get_approved_user)):
    from app.services.speech import synthesize

    text = body.text.strip()
    data = await synthesize(text)
    if data:
        return Response(content=data, media_type="audio/mpeg")
    return {"mode": "browser", "text": text}
