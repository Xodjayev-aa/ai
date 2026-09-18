"""Presentations: AI outline -> JSON slides (web view) or .pptx download."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.ai import client
from app.ai import config as ai_config
from app.ai.presentations import generate_outline, outline_to_pptx
from app.database import bump_usage
from app.deps import get_current_user

router = APIRouter(prefix="/api/presentations", tags=["presentations"])


class OutlineBody(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    num_slides: int = Field(default=8, ge=3, le=20)
    audience: str = Field(default="general audience", max_length=60)
    tone: str = Field(default="professional but engaging", max_length=60)
    language: str = Field(default="English", max_length=30)


class PptxBody(BaseModel):
    outline: dict
    theme: str = "ocean"


@router.post("/generate")
async def create_outline(body: OutlineBody, user=Depends(get_current_user)):
    if get_usage_guard(user):
        raise HTTPException(status_code=429, detail="Daily AI request limit reached.")
    try:
        outline = await generate_outline(
            body.topic,
            num_slides=body.num_slides,
            audience=body.audience,
            tone=body.tone,
            language=body.language,
        )
    except client.AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AI returned an unreadable outline ({exc}). Try again.",
        )
    bump_usage(user["id"])
    return outline


@router.post("/pptx")
async def export_pptx(body: PptxBody, user=Depends(get_current_user)):
    outline = body.outline
    if not isinstance(outline.get("slides"), list) or not outline["slides"]:
        raise HTTPException(status_code=400, detail="outline.slides is required")
    try:
        data = outline_to_pptx(outline, theme=body.theme)
    except Exception as exc:  # noqa: BLE001 — surface a clean error
        raise HTTPException(status_code=500,
                            detail=f"Could not build .pptx: {exc}")
    safe_title = quote(outline.get("title") or "presentation")[:80] or "presentation"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument"
                   ".presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.pptx"'},
    )


def get_usage_guard(user) -> bool:
    from app.database import get_usage
    return get_usage(user["id"]) >= ai_config.DAILY_REQUEST_LIMIT
