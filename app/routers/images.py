"""Image generation via Pollinations.ai — free, no API key required."""

import re
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.ai import config
from app.database import bump_usage
from app.deps import get_current_user

router = APIRouter(prefix="/api/images", tags=["images"])

_DIM = re.compile(r"^[0-9]{1,4}$")


class ImageBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=600)
    width: int = Field(default=1024, ge=256, le=1280)
    height: int = Field(default=1024, ge=256, le=1280)
    model: str | None = None


@router.post("/generate")
async def generate_image(body: ImageBody, user=Depends(get_current_user)):
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    seed = secrets.randbelow(1_000_000)
    model = body.model if body.model in ("flux", "turbo") else config.IMAGE_MODEL
    url = (
        f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
        f"?width={body.width}&height={body.height}&seed={seed}"
        f"&model={model}&nologo=true"
    )
    if config.POLLINATIONS_TOKEN:
        url += f"&token={config.POLLINATIONS_TOKEN}"

    bump_usage(user["id"])
    return {
        "url": url,
        "prompt": prompt,
        "seed": seed,
        "width": body.width,
        "height": body.height,
        "model": model,
        "note": "Free Pollinations generation — first load can take ~10-30s.",
    }
