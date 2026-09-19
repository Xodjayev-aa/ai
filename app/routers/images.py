"""Image generation via Pollinations.ai — free, no API key required."""

import re
import secrets
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
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


ALLOWED_IMAGE_HOSTS = ("image.pollinations.ai",)


@router.get("/download")
async def download_image(url: str = Query(..., max_length=2000),
                         name: str = Query("aether-image", max_length=60)):
    """Proxy a generated image so the browser can save it with a real filename.

    (A cross-origin `download` attribute is ignored by browsers, and this keeps
    the generated image reachable even if the provider's URL expires.)
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_IMAGE_HOSTS:
        raise HTTPException(status_code=400, detail="Only generated images can be downloaded")
    safe = re.sub(r"[^\w\-. ]+", "", name).strip() or "aether-image"
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Image download failed: {exc}")
    if resp.status_code != 200 or not resp.content:
        raise HTTPException(status_code=502, detail="Image download failed")
    ctype = resp.headers.get("content-type", "image/jpeg")
    ext = {"image/png": ".png", "image/webp": ".webp"}.get(ctype.split(";")[0], ".jpg")
    return Response(content=resp.content, media_type=ctype, headers={
        "Content-Disposition": f'attachment; filename="{safe}{ext}"',
        "Cache-Control": "private, max-age=86400",
    })


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
