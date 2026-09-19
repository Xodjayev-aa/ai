"""Public read-only shared chats: /share/{token} — token is the capability."""

import html
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.database import create_share, delete_share, get_shared
from app.deps import get_current_user

router = APIRouter(tags=["share"])


class ShareBody(BaseModel):
    pass


@router.post("/api/conversations/{conv_id}/share")
def share_conversation(conv_id: str, user=Depends(get_current_user)):
    token = create_share(conv_id, user["id"])
    if not token:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"token": token, "path": f"/share/{token}"}


@router.delete("/api/conversations/{conv_id}/share")
def unshare_conversation(conv_id: str, user=Depends(get_current_user)):
    if delete_share(conv_id, user["id"]) == 0:
        raise HTTPException(status_code=404, detail="Nothing shared for this chat")
    return {"status": "unshared"}


def _md_basic(text: str) -> str:
    """Tiny safe markdown-ish renderer for the share page."""
    out = html.escape(text)
    out = out.replace("```", "\n```\n")
    parts = out.split("```")
    for i, part in enumerate(parts):
        if i % 2 == 1:
            parts[i] = f"<pre>{part}</pre>"
        else:
            part = part.replace("**", "\x01").replace("\x01", "**")
            lines = []
            for line in part.split("\n"):
                if line.startswith("### "): lines.append(f"<h3>{line[4:]}</h3>")
                elif line.startswith("## "): lines.append(f"<h2>{line[3:]}</h2>")
                elif line.startswith("# "): lines.append(f"<h1>{line[2:]}</h1>")
                elif line.startswith("- ") or line.startswith("* "):
                    lines.append(f"<li>{line[2:]}</li>")
                else: lines.append(f"<p>{line}</p>")
            parts[i] = "".join(lines)
    return "".join(parts)


@router.get("/share/{token}", response_class=HTMLResponse)
def shared_chat(token: str):
    data = get_shared(token)
    if not data:
        return HTMLResponse("<h1>404</h1><p>This shared chat does not exist "
                            "(or sharing was turned off).</p>", status_code=404)
    msgs = "".join(
        f'<div class="msg {m["role"]}"><div class="who">'
        f'{"You" if m["role"] == "user" else "Aether"}</div>'
        f'<div class="bubble">{_md_basic(m["content"])}</div></div>'
        for m in data["messages"])
    title = html.escape(data["title"])
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — shared from Aether</title>
<style>
body{{background:#0b1020;color:#e8ecff;font-family:system-ui,sans-serif;margin:0;padding:32px 14px}}
main{{max-width:760px;margin:0 auto}}
h1{{font-size:22px;margin-bottom:4px}} .sub{{color:#8b93b8;font-size:13px;margin-bottom:24px}}
.msg{{margin:14px 0}} .who{{font-size:12px;color:#8b93b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.1em}}
.assistant .who{{color:#6c8cff}}
.bubble{{line-height:1.65;font-size:15px;word-wrap:break-word}}
.bubble pre{{background:#0a0e1e;border:1px solid #242c52;border-radius:10px;padding:12px;overflow-x:auto}}
.bubble h1,.bubble h2,.bubble h3{{margin:10px 0 6px}} .bubble li{{margin:4px 0 4px 20px}}
footer{{margin-top:40px;color:#8b93b8;font-size:13px;border-top:1px solid #242c52;padding-top:16px}}
</style></head><body><main>
<h1>{title}</h1>
<p class="sub">Shared conversation · {len(data['messages'])} messages · created {data['created_at']}</p>
{msgs}
<footer>✨ Shared from <b>Aether</b> — read-only view.</footer>
</main></body></html>""")


# ═══ news router (keyless Google News RSS) ═══
NEWSCAT = """
"""
