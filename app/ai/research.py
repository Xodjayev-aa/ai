"""Research mode — real information with ZERO AI and ZERO API keys.

Sources (all free, keyless):
  - Wikipedia REST search + plain-text extracts
  - DuckDuckGo Instant Answer API (abstract + related topics)

The answer is COMPOSED (templated) from retrieved passages — no language
model involved — and always includes citations with links.
"""

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

_UA = {"User-Agent": "AetherPWA/2.0 (educational project)"}
_TIMEOUT = 15


@dataclass
class Source:
    title: str
    url: str
    snippet: str = ""
    source: str = "wikipedia"


@dataclass
class ResearchResult:
    query: str
    sources: list[Source] = field(default_factory=list)
    answer_md: str = ""


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    # trim listening-stop trailing sections
    return text


def _sentences(text: str, limit: int) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", _clean(text))
    return [s for s in parts if len(s) > 25][:limit]


# ------------------------------------------------------------ Wikipedia

async def wiki_search(client: httpx.AsyncClient, query: str,
                      limit: int = 4) -> list[Source]:
    resp = await client.get(
        "https://en.wikipedia.org/w/rest.php/v1/search/page",
        params={"q": query, "limit": limit},
    )
    resp.raise_for_status()
    pages = resp.json().get("pages", [])
    return [Source(
        title=p.get("title", ""),
        url=f"https://en.wikipedia.org/wiki/{quote((p.get('key') or p.get('title','')).replace(' ', '_'))}",
        snippet=_clean(p.get("description", "")),
        source="wikipedia",
    ) for p in pages]


async def wiki_extract(client: httpx.AsyncClient, title: str,
                       sentences: int = 6) -> str:
    resp = await client.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "format": "json", "prop": "extracts",
            "explaintext": 1, "exsectionformat": "plain",
            "redirects": 1, "titles": title,
        },
    )
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", {})
    for page in pages.values():
        extract = page.get("extract", "")
        if extract:
            # skip disambiguation-ish pages
            if "may refer to" in extract[:200]:
                return ""
            return " ".join(_sentences(extract, sentences))
    return ""


# -------------------------------------------------------- DuckDuckGo IA

async def ddg_instant(client: httpx.AsyncClient, query: str) -> tuple[str, list[Source]]:
    resp = await client.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
    )
    resp.raise_for_status()
    data = resp.json()
    abstract = _clean(data.get("AbstractText") or data.get("Abstract") or "")
    sources = []
    if abstract and data.get("AbstractURL"):
        sources.append(Source(title=data.get("Heading") or "DuckDuckGo",
                              url=data["AbstractURL"],
                              snippet=abstract[:220], source="duckduckgo"))
    for rt in (data.get("RelatedTopics") or [])[:4]:
        if isinstance(rt, dict) and rt.get("Text") and rt.get("FirstURL"):
            sources.append(Source(title=rt["Text"][:70],
                                  url=rt["FirstURL"],
                                  snippet=rt["Text"][:220],
                                  source="duckduckgo"))
    return abstract, sources


# ------------------------------------------------------------- compose

def compose(query: str, wiki_passages: list[tuple[Source, str]],
            ddg_abstract: str, ddg_sources: list[Source]) -> ResearchResult:
    sources: list[Source] = []
    sections: list[str] = []

    for src, passage in wiki_passages:
        if not passage:
            continue
        sections.append(f"### {src.title}\n{passage}")
        sources.append(src)
    if ddg_abstract:
        sections.append(f"### Quick facts\n{ddg_abstract}")
        sources.extend(s for s in ddg_sources if s not in sources)

    if not sections:
        answer = (
            f"No reliable results found for **{query}**.\n\nTry rephrasing with "
            "more specific keywords, or switch to ⚡ AI mode (works keyless via "
            "Pollinations when the network allows)."
        )
    else:
        intro = f"Here's what the sources say about **{query}**:\n"
        listing = "\n\n".join(sections)
        cite_list = "\n".join(
            f"{i+1}. [{s.title}]({s.url})" + (f" — {s.snippet}" if s.snippet else "")
            for i, s in enumerate(sources[:8])
        )
        answer = (intro + "\n\n" + listing +
                  "\n\n## Sources\n" + cite_list +
                  "\n\n*Research mode retrieves real text — no AI generation, "
                  "so wording comes straight from the cited sources.*")
    return ResearchResult(query=query, sources=sources, answer_md=answer)


async def research(query: str) -> ResearchResult:
    """Fetch + compose. Individual source failures degrade gracefully."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA,
                                 follow_redirects=True) as client:

        async def safe(coro):
            try:
                return await coro
            except (httpx.HTTPError, KeyError, ValueError):
                return None

        hits, abstract_and_srcs = await asyncio.gather(
            safe(wiki_search(client, query)),
            safe(ddg_instant(client, query)),
        )
        hits = hits or []
        ddg_abstract, ddg_sources = abstract_and_srcs or ("", [])

        extracts = await asyncio.gather(*[
            safe(wiki_extract(client, s.title)) for s in hits[:3]
        ])
        passages = list(zip(hits[:3], [e or "" for e in extracts]))
        return compose(query, passages, ddg_abstract, ddg_sources)


# ------------------------------------------------------- article reading

async def extract_article(url: str) -> dict:
    """Fetch a web page and extract readable text (no AI, no keys)."""
    from html import unescape

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA,
                                 follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    page = resp.text
    title_m = re.search(r"<title[^>]*>(.*?)</title>", page, re.IGNORECASE | re.DOTALL)
    title = unescape(_clean(title_m.group(1)))[:200] if title_m else url
    # strip non-content blocks then tags
    page = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav|form)[^>]*>.*?</\1>",
                  " ", page)
    text = re.sub(r"(?s)<[^>]+>", " ", page)
    text = unescape(_clean(re.sub(r"\s+", " ", text)))
    # heuristic body start: skip boilerplate heads
    words = text.split()
    return {"url": url, "title": title or url,
            "text": " ".join(words[:4000])}
