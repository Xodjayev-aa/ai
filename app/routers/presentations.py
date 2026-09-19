"""Presentations, generated *incrementally*.

A single "write me a 10-slide deck" AI call is slow (and on the free shared
tier, a single hiccup loses everything). So a deck is built in small steps:

    POST /api/presentations/plan          -> deck skeleton (one AI call)
    POST /api/presentations/slide         -> one slide, persisted (per call)
    GET  /api/presentations/deck/{id}     -> resume/refetch the whole deck
    POST /api/presentations/pptx|html     -> export from a deck id or outline

Every step is idempotent per (deck, index), so the UI can retry a single slide
without touching the ones that already worked, and a deck survives page
reloads because it lives in the database.
"""

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.ai import client
from app.ai import config as ai_config
from app.ai.decks import outline_to_html
from app.ai.presentations import outline_to_pptx
from app.database import (
    bump_usage, create_deck, delete_deck, get_deck, get_usage, list_decks,
    save_deck_outline,
)
from app.deps import get_current_user

router = APIRouter(prefix="/api/presentations", tags=["presentations"])

PLAN_PROMPT = (
    "You are an expert presentation designer. Design the STRUCTURE of a slide "
    "deck about the given topic. Reply with ONLY valid JSON (no markdown "
    "fences, no prose) in this exact shape:\n"
    '{{"title": "deck title", "subtitle": "one-line subtitle", '
    '"slides": [{{"title": "slide title"}}, ...]}}\n'
    "Rules: exactly {num_slides} slide titles, in a logical order (strong "
    "opening, clear middle, memorable closing), each title under 60 "
    "characters and specific to the topic. Audience: {audience}. "
    "Tone: {tone}. Language: {language}."
)

SLIDE_PROMPT = (
    "You are an expert presentation designer writing ONE slide of a deck.\n"
    "Deck title: {deck_title}\nSubtitle: {deck_subtitle}\nTopic: {topic}\n"
    "Audience: {audience}\nPosition: slide {index} of {total}\n"
    "This slide's title: {slide_title}\n"
    "Other slide titles for context: {siblings}\n\n"
    "Reply with ONLY valid JSON (no fences, no prose):\n"
    '{{"title": "{slide_title}", "bullets": ["point 1", "point 2", "point 3"], '
    '"notes": "1-3 sentences of speaker notes"}}\n'
    "Rules: 3-5 bullets, each under 90 characters, concrete and specific "
    "(numbers, names, examples where they help); do not repeat the title in "
    "the bullets; notes are what the speaker would say out loud. Language: "
    "{language}. Tone: {tone}."
)


class PlanBody(BaseModel):
    topic: str = Field(min_length=2, max_length=2000)
    num_slides: int = Field(default=8, ge=3, le=20)
    audience: str = Field(default="general audience", max_length=60)
    tone: str = Field(default="professional but engaging", max_length=60)
    language: str = Field(default="English", max_length=30)


class SlideBody(BaseModel):
    deck_id: str
    index: int = Field(ge=0, le=29)
    feedback: str | None = Field(default=None, max_length=300)


class ExportBody(BaseModel):
    outline: dict | None = None
    deck_id: str | None = None
    theme: str = "ocean"


class OutlineBody(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    num_slides: int = Field(default=8, ge=3, le=20)
    audience: str = Field(default="general audience", max_length=60)
    tone: str = Field(default="professional but engaging", max_length=60)
    language: str = Field(default="English", max_length=30)


# --------------------------------------------------------------- helpers

def _extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON in the model reply")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON was not an object")
    return data


async def _ask_json(system: str, user: str) -> dict:
    raw = await client.complete(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.5,
    )
    try:
        return _extract_json(raw)
    except (ValueError, json.JSONDecodeError):
        repaired = await client.complete(
            [{"role": "system", "content": "Reply with ONLY minified valid JSON."},
             {"role": "user", "content": "Fix this into valid JSON, same content:\n"
                                         + raw[:6000]}],
            temperature=0.0,
        )
        return _extract_json(repaired)


def _clean_bullets(raw: object, limit: int = 6) -> list[str]:
    bullets = []
    for item in (raw or []) if isinstance(raw, (list, tuple)) else []:
        text = str(item).strip().lstrip("-•* ").strip()
        if text:
            bullets.append(text[:160])
    return bullets[:limit]


def _usage_guard(user) -> None:
    if get_usage(user["id"]) >= ai_config.DAILY_REQUEST_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=(f"Daily limit of {ai_config.DAILY_REQUEST_LIMIT} AI requests "
                    "reached. It resets at midnight UTC."),
        )


def _resolve_outline(body: ExportBody, user) -> dict:
    if body.deck_id:
        deck = get_deck(body.deck_id, user["id"])
        if not deck:
            raise HTTPException(status_code=404, detail="Deck not found")
        outline = deck.get("outline") or {}
        if not outline.get("slides"):
            raise HTTPException(status_code=409,
                                detail="Deck has no slides yet — generate them first.")
        return outline
    if body.outline and isinstance(body.outline.get("slides"), list) and body.outline["slides"]:
        return body.outline
    raise HTTPException(status_code=400,
                        detail="Provide deck_id or an outline with slides")


# ------------------------------------------------------------------ plan

@router.post("/plan")
async def create_plan(body: PlanBody, user=Depends(get_current_user)):
    """One AI call: deck title + slide titles. Fast, so failures are cheap."""
    _usage_guard(user)
    prompt = PLAN_PROMPT.format(
        num_slides=max(3, min(body.num_slides, 20)),
        audience=body.audience[:60], tone=body.tone[:60],
        language=body.language[:30],
    )
    try:
        data = await _ask_json(prompt, body.topic[:2000])
    except client.AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502,
                            detail=f"AI returned an unreadable outline ({exc}). Try again.")

    raw_slides = data.get("slides") or []
    titles: list[str] = []
    for item in raw_slides:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
        else:
            title = str(item).strip()
        if title:
            titles.append(title[:120])
    if not titles:
        raise HTTPException(status_code=502,
                            detail="AI returned no slide titles. Try again.")
    titles = titles[:20]

    outline = {
        "title": str(data.get("title") or body.topic)[:150],
        "subtitle": str(data.get("subtitle") or "")[:200],
        "topic": body.topic[:2000],
        "audience": body.audience[:60],
        "tone": body.tone[:60],
        "language": body.language[:30],
        "slides": [None] * len(titles),
        "plan": titles,
    }
    deck = create_deck(user["id"], outline["title"], outline["subtitle"],
                       body.topic, outline, status="planning")
    bump_usage(user["id"])
    return {
        "deck_id": deck["id"],
        "title": outline["title"],
        "subtitle": outline["subtitle"],
        "slides": [{"index": i, "title": t} for i, t in enumerate(titles)],
        "num_slides": len(titles),
        "usage": {"used_today": get_usage(user["id"]),
                  "limit": ai_config.DAILY_REQUEST_LIMIT},
    }


# ----------------------------------------------------------------- slide

@router.post("/slide")
async def create_slide(body: SlideBody, user=Depends(get_current_user)):
    """Generate (or retry) exactly one slide — a few seconds per call."""
    _usage_guard(user)
    deck = get_deck(body.deck_id, user["id"])
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    outline = deck.get("outline") or {}
    plan: list[str] = outline.get("plan") or []
    if not plan:
        raise HTTPException(status_code=409, detail="Deck has no plan")
    if body.index >= len(plan):
        raise HTTPException(status_code=400,
                            detail=f"Slide index out of range (deck has {len(plan)})")

    siblings = [f"{i + 1}. {t}" for i, t in enumerate(plan) if i != body.index]
    prompt = SLIDE_PROMPT.format(
        deck_title=outline.get("title", ""),
        deck_subtitle=outline.get("subtitle", ""),
        topic=outline.get("topic", ""),
        audience=outline.get("audience", "general audience"),
        index=body.index + 1, total=len(plan),
        slide_title=plan[body.index],
        siblings="; ".join(siblings[:14]),
        language=outline.get("language", "English"),
        tone=outline.get("tone", "professional but engaging"),
    )
    user_msg = f"Write slide {body.index + 1} now."
    if body.feedback:
        user_msg += (f"\nThe user asked for this change on this slide: "
                     f"{body.feedback.strip()}")
    try:
        data = await _ask_json(prompt, user_msg)
    except client.AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502,
                            detail=f"AI returned an unreadable slide ({exc}). Retry the slide.")

    slide = {
        "title": str(data.get("title") or plan[body.index])[:120],
        "bullets": _clean_bullets(data.get("bullets")),
        "notes": str(data.get("notes") or "")[:600],
    }
    if not slide["bullets"]:
        slide["bullets"] = [plan[body.index]]

    slides = list(outline.get("slides") or [])
    while len(slides) <= body.index:
        slides.append(None)
    slides[body.index] = slide
    outline["slides"] = slides
    done = sum(1 for s in slides if s)
    save_deck_outline(deck["id"], user["id"], outline,
                      status="ready" if done == len(plan) else "partial")
    bump_usage(user["id"])
    return {
        **slide,
        "index": body.index,
        "done": done,
        "total": len(plan),
        "usage": {"used_today": get_usage(user["id"]),
                  "limit": ai_config.DAILY_REQUEST_LIMIT},
    }


# ----------------------------------------------------------------- decks

@router.get("/decks")
def decks(user=Depends(get_current_user)):
    return list_decks(user["id"])


@router.get("/deck/{deck_id}")
def deck_detail(deck_id: str, user=Depends(get_current_user)):
    deck = get_deck(deck_id, user["id"])
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    outline = deck.get("outline") or {}
    plan = outline.get("plan") or []
    slides = outline.get("slides") or []
    slides = [s for s in slides if s]
    return {
        "deck_id": deck["id"],
        "title": deck["title"],
        "subtitle": deck["subtitle"],
        "status": deck["status"],
        "plan": plan,
        "slides": outline.get("slides") or [],
        "complete": len(slides) == len(plan) and bool(plan),
        "outline": {**outline, "slides": slides} if slides else None,
    }


@router.delete("/deck/{deck_id}")
def deck_delete(deck_id: str, user=Depends(get_current_user)):
    if delete_deck(deck_id, user["id"]) == 0:
        raise HTTPException(status_code=404, detail="Deck not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------- export

@router.post("/pptx")
def export_pptx(body: ExportBody, user=Depends(get_current_user)):
    outline = _resolve_outline(body, user)
    try:
        data = outline_to_pptx(outline, theme=body.theme)
    except Exception as exc:  # noqa: BLE001 — surface a clean error
        raise HTTPException(status_code=500,
                            detail=f"Could not build .pptx: {exc}")
    safe_title = (outline.get("title") or "presentation").replace('"', "")[:80]
    return Response(
        content=data,
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".presentationml.presentation"),
        headers={
            "Content-Disposition": f'attachment; filename="{safe_title}.pptx"',
            "X-Slides": str(len(outline.get("slides") or [])),
        },
    )


@router.post("/html")
def export_html(body: ExportBody, user=Depends(get_current_user)):
    """Standalone HTML deck (self-contained, keyboard navigation, printable)."""
    outline = _resolve_outline(body, user)
    try:
        page = outline_to_html(outline, theme=body.theme)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"Could not build HTML deck: {exc}")
    safe_title = (outline.get("title") or "presentation").replace('"', "")[:80]
    return Response(
        content=page,
        media_type="text/html",
        headers={"Content-Disposition":
                 f'attachment; filename="{safe_title}-deck.html"'},
    )


# ------------------------------------------------- legacy single-shot API

@router.post("/generate")
async def create_outline(body: OutlineBody, user=Depends(get_current_user)):
    """Old single-shot endpoint, kept for compatibility (and as a fallback).

    New clients should use /plan + /slide, which stream progress slide by slide.
    """
    _usage_guard(user)
    from app.ai.presentations import generate_outline

    try:
        outline = await generate_outline(
            body.topic, num_slides=body.num_slides, audience=body.audience,
            tone=body.tone, language=body.language,
        )
    except client.AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=502,
                            detail=f"AI returned an unreadable outline ({exc}). Try again.")
    bump_usage(user["id"])
    return outline
