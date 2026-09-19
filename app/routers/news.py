"""News headlines via Google News RSS — free, keyless, no AI."""

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_current_user

router = APIRouter(prefix="/api/news", tags=["news"])

_UA = {"User-Agent": "AetherPWA/2.0"}
_TAG = re.compile(r"<[^>]+>")


def _parse_items(xml_text: str, limit: int = 12) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter("item")[:limit]:
        title = item.findtext("title") or ""
        source = item.findtext("source") or ""
        items.append({
            "title": _TAG.sub("", title),
            "link": item.findtext("link") or "",
            "source": _TAG.sub("", source),
            "published": item.findtext("pubDate") or "",
        })
    return items


@router.get("")
async def headlines(q: str = Query(default="", max_length=120),
                    user=Depends(get_current_user)):
    url = (f"https://news.google.com/rss/search?q={quote(q)}"
           f"&hl=en-US&gl=US&ceid=US:en" if q.strip()
           else "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en")
    try:
        async with httpx.AsyncClient(timeout=15, headers=_UA,
                                     follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503,
                            detail=f"News source unreachable: {exc}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="News source failed")
    try:
        return {"topic": q or "top stories", "items": _parse_items(resp.text)}
    except ET.ParseError:
        raise HTTPException(status_code=502, detail="Could not parse news feed")


# ═══ scheduled tasks router + daily cron ═══
