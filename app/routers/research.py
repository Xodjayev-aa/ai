"""Standalone research API — real sources, zero AI, zero keys.

GET /api/research/search?q=photosynthesis
Returns composed markdown + structured sources (Wikipedia, DuckDuckGo).
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.ai import research as research_mod
from app.deps import get_current_user

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=300),
                 user=Depends(get_current_user)):
    try:
        result = await research_mod.research(q)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503,
                            detail=f"Research sources unreachable: {exc}")
    return {
        "query": result.query,
        "answer_md": result.answer_md,
        "sources": [{
            "title": s.title, "url": s.url, "snippet": s.snippet,
            "engine": s.source,
        } for s in result.sources],
        "found": bool(result.sources),
    }
