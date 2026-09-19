"""Probe the keyless free-tier providers and print what actually works.

This is *informational*: it tells us which Pollinations text/TTS/image
endpoints answer, what models and voices exist, and how the free tier
rate-limits us. Run it on a machine with real internet:

    python tests/probe_pollinations.py
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx

TEXT_BASES = [
    "https://text.pollinations.ai",
    "https://gen.pollinations.ai/v1",
]


async def probe_bases() -> list[str]:
    working = []
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as c:
        for base in TEXT_BASES:
            try:
                r = await c.get(f"{base}/models")
                if r.status_code == 200:
                    try:
                        models = r.json()
                        if isinstance(models, list):
                            names = [m.get("name") or m.get("id") for m in models
                                     if isinstance(m, dict)]
                        else:
                            names = list(models)
                    except Exception:  # noqa: BLE001
                        names = r.text[:200]
                    print(f"[ok]   {base}/models -> {json.dumps(names)[:400]}")
                    working.append(base)
                else:
                    print(f"[fail] {base}/models -> HTTP {r.status_code}")
            except Exception as exc:  # noqa: BLE001
                print(f"[fail] {base}/models -> {exc}")
    return working


async def probe_text(bases: list[str]) -> None:
    body_common = {
        "messages": [{"role": "user", "content": "Say 'pong' and nothing else."}],
        "temperature": 0.2,
        "private": True,
    }
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as c:
        for base in bases:
            t0 = time.time()
            try:
                r = await c.post(f"{base}/openai",
                                 json={**body_common, "model": "openai", "stream": False})
                print(f"[{ 'ok' if r.status_code == 200 else 'fail' }] POST {base}/openai "
                      f"(model=openai) -> HTTP {r.status_code} in {time.time() - t0:.1f}s "
                      f"{r.text[:200]!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"[fail] POST {base}/openai -> {exc}")

            t0 = time.time()
            first = None
            try:
                async with c.stream("POST", f"{base}/openai",
                                    json={**body_common, "model": "openai", "stream": True}) as r:
                    if r.status_code != 200:
                        print(f"[fail] stream {base}/openai -> HTTP {r.status_code}")
                        continue
                    async for line in r.aiter_lines():
                        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                            if first is None:
                                first = time.time() - t0
                            break
                print(f"[ok]   stream {base}/openai -> first delta in "
                      f"{first if first is not None else '?'}s")
            except Exception as exc:  # noqa: BLE001
                print(f"[fail] stream {base}/openai -> {exc}")


async def probe_tts(bases: list[str]) -> None:
    voices = ["alloy", "nova", "shimmer", "echo", "fable", "onyx", "ash", "coral", "sage"]
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get("https://text.pollinations.ai/models")
        print(f"[info] models endpoint says: {r.text[:600]!r}")
        for base in bases:
            for voice in voices:
                t0 = time.time()
                try:
                    resp = await c.get(f"{base}/Hello%20there%2C%20a%20quick%20voice%20test."
                                       f"?model=openai-audio&voice={voice}")
                    ct = resp.headers.get("content-type", "")
                    print(f"[{ 'ok' if resp.status_code == 200 and 'audio' in ct else 'fail' }] "
                          f"TTS {base} voice={voice} -> HTTP {resp.status_code} {ct} "
                          f"{len(resp.content)}B in {time.time() - t0:.1f}s")
                except Exception as exc:  # noqa: BLE001
                    print(f"[fail] TTS {base} voice={voice} -> {exc}")


async def probe_images() -> None:
    url = ("https://image.pollinations.ai/prompt/simple%20blue%20circle%20on%20dark%20"
           "background?width=512&height=512&model=flux&nologo=true&seed=1")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        t0 = time.time()
        for model in ("flux", "turbo"):
            u = url.replace("model=flux", f"model={model}")
            try:
                r = await c.get(u)
                print(f"[{ 'ok' if r.status_code == 200 else 'fail' }] image model={model} -> "
                      f"HTTP {r.status_code} {r.headers.get('content-type')} "
                      f"{len(r.content)}B in {time.time() - t0:.1f}s")
            except Exception as exc:  # noqa: BLE001
                print(f"[fail] image model={model} -> {exc}")


async def main() -> None:
    print("== Pollinations text bases ==")
    bases = await probe_bases()
    if not bases:
        print("no text base reachable — keyless chat cannot work here")
        return
    print("\n== Text completion ==")
    await probe_text(bases)
    print("\n== TTS (openai-audio) ==")
    await probe_tts(bases[:1])
    print("\n== Images ==")
    await probe_images()


if __name__ == "__main__":
    asyncio.run(main())
