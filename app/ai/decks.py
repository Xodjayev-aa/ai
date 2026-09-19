"""Canva-style standalone HTML deck generation.

Produces a single self-contained .html file (no external CSS/JS frameworks
— those CDN links in tutorials break; we embed everything) with:
  - gradient theme, cover image per slide via keyless Pollinations images
  - keyboard/click navigation, progress dots, print-to-PDF stylesheet
"""

import html
import secrets
from urllib.parse import quote

THEME_STYLES = {
    "ocean": ("#0b1f3a", "#123057", "#3ec1d3", "#eaf6ff"),
    "ember": ("#200f0a", "#3a1c12", "#ff7a45", "#fff3ec"),
    "forest": ("#0c2417", "#153826", "#6fcf97", "#f0fff6"),
    "violet": ("#170d2b", "#2a1a4a", "#a78bfa", "#f6f1ff"),
}


def _img(prompt: str, w: int = 1280, h: int = 720, seed: int | None = None) -> str:
    seed = seed if seed is not None else secrets.randbelow(1_000_000)
    return (f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
            f"?width={w}&height={h}&seed={seed}&nologo=true")


def outline_to_html(outline: dict, theme: str = "ocean") -> str:
    bg, bg2, accent, text = THEME_STYLES.get(theme, THEME_STYLES["ocean"])
    title = html.escape(str(outline.get("title") or "Presentation"))
    subtitle = html.escape(str(outline.get("subtitle") or ""))

    slides_html = []

    # ---- Cover slide
    cover_img = _img(f"minimalist elegant abstract illustration for a presentation"
                     f" cover about {outline.get('title') or 'a topic'}, "
                     f"dark background, gradient, modern, no text")
    slides_html.append(f"""
    <section class="slide cover">
      <div class="cover-text">
        <span class="kicker">Presentation</span>
        <h1>{title}</h1>
        <p class="sub">{subtitle}</p>
      </div>
      <div class="cover-img"><img loading="lazy" src="{cover_img}" alt=""></div>
    </section>""")

    # ---- Content slides
    for idx, s in enumerate(outline.get("slides", [])):
        st = html.escape(str(s.get("title") or ""))
        bullets = "".join(
            f"<li>{html.escape(str(b))}</li>"
            for b in (s.get("bullets") or [])[:6]
        )
        notes = html.escape(str(s.get("notes") or ""))
        img = _img(f"clean minimal flat illustration representing {st}, "
                   f"no text, dark background, {theme} color palette", seed=idx * 7 + 3)
        notes_html = f'<p class="notes">{notes}</p>' if notes else ""
        slides_html.append(f"""
    <section class="slide">
      <div class="slide-main">
        <span class="num">{idx + 1:02d}</span>
        <h2>{st}</h2>
        <ul>{bullets}</ul>
        {notes_html}
      </div>
      <div class="slide-img"><img loading="lazy" src="{img}" alt=""></div>
    </section>""")

    # ---- Closing slide
    slides_html.append(f"""
    <section class="slide closing">
      <h2>Thank you</h2>
      <p>{title}</p>
    </section>""")

    n = len(slides_html)
    body = "\n".join(slides_html)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#05070f; color:{text}; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; overflow:hidden; }}
  .deck {{ position:relative; width:100vw; height:100dvh; }}
  .slide {{
    position:absolute; inset:0; display:none; padding:6vh 7vw;
    background: radial-gradient(1200px 800px at 80% -10%, {bg2} 0%, {bg} 60%);
  }}
  .slide.active {{ display:flex; animation: fade .35s ease; }}
  @keyframes fade {{ from {{ opacity:0; transform: translateY(8px); }} to {{ opacity:1; }} }}
  .cover {{ align-items:center; gap:5vw; }}
  .cover-text {{ flex:1.1; }}
  .kicker {{ color:{accent}; letter-spacing:.2em; text-transform:uppercase; font-size:clamp(11px,1.4vw,14px); font-weight:700; }}
  .cover h1 {{ font-size:clamp(30px,5.5vw,64px); line-height:1.1; margin:14px 0; }}
  .cover .sub {{ color:color-mix(in srgb, {text} 70%, transparent); font-size:clamp(14px,1.8vw,20px); }}
  .cover-img, .slide-img {{ flex:1; min-width:0; border-radius:20px; overflow:hidden; border:1px solid {accent}44;
    box-shadow:0 30px 80px rgba(0,0,0,.5); align-self:center; }}
  .cover-img img, .slide-img img {{ width:100%; height:100%; object-fit:cover; display:block; min-height:200px; }}
  .slide-main {{ flex:1.2; min-width:0; }}
  .num {{ color:{accent}; font-weight:800; letter-spacing:.15em; font-size:clamp(11px,1.3vw,14px); }}
  .slide h2 {{ font-size:clamp(24px,3.6vw,42px); margin:10px 0 22px; border-left:6px solid {accent}; padding-left:16px; }}
  .slide ul {{ list-style:none; }}
  .slide li {{ font-size:clamp(15px,2vw,22px); margin:0 0 16px; padding-left:30px; position:relative; line-height:1.5; }}
  .slide li::before {{ content:"▸"; position:absolute; left:0; color:{accent}; }}
  .notes {{ color:color-mix(in srgb, {text} 60%, transparent); font-size:clamp(12px,1.4vw,15px);
    border-top:1px dashed {accent}55; padding-top:12px; margin-top:8px; }}
  .closing {{ flex-direction:column; align-items:center; justify-content:center; text-align:center; }}
  .closing h2 {{ font-size:clamp(36px,6vw,72px); color:{accent}; border:none; padding:0; }}
  .closing p {{ margin-top:14px; opacity:.7; }}
  .dots {{ position:fixed; bottom:18px; left:50%; transform:translateX(-50%); display:flex; gap:8px; z-index:5; }}
  .dots i {{ width:8px; height:8px; border-radius:50%; background:{accent}44; transition:.2s; }}
  .dots i.on {{ background:{accent}; transform:scale(1.3); }}
  .hint {{ position:fixed; bottom:14px; right:18px; font-size:12px; opacity:.45; }}
  @media (max-width:760px) {{
    .slide {{ flex-direction:column; overflow-y:auto; padding:5vh 6vw; }}
    .slide-img, .cover-img {{ max-height:32vh; }}
  }}
  @media print {{
    body {{ overflow:visible; }}
    .deck {{ width:auto; height:auto; }}
    .slide {{ display:flex !important; position:relative; page-break-after:always; width:100vw; height:100vh; }}
    .dots, .hint {{ display:none; }}
  }}
</style>
</head>
<body>
<div class="deck" id="deck">{body}
  <div class="dots" id="dots">{''.join('<i class="on"></i>' if i == 0 else '<i></i>' for i in range(n))}</div>
  <div class="hint">← → keys · click to advance · Ctrl/Cmd+P to PDF</div>
</div>
<script>
(function() {{
  var slides = document.querySelectorAll('.slide');
  var dots = document.querySelectorAll('#dots i');
  var i = 0;
  function show(k) {{
    i = Math.max(0, Math.min(slides.length - 1, k));
    slides.forEach(function(s, j) {{ s.classList.toggle('active', j === i); }});
    dots.forEach(function(d, j) {{ d.classList.toggle('on', j === i); }});
  }}
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown') {{ show(i + 1); e.preventDefault(); }}
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') {{ show(i - 1); e.preventDefault(); }}
    if (e.key === 'Home') show(0);
    if (e.key === 'End') show(slides.length - 1);
  }});
  document.getElementById('deck').addEventListener('click', function(e) {{
    if (e.clientX > window.innerWidth * 0.3) show(i + 1); else show(i - 1);
  }});
  show(0);
}})();
</script>
</body>
</html>"""
