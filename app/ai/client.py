"""Async OpenAI-compatible chat client with automatic key rotation."""

import json
import typing

import httpx

from app.ai import config
from app.ai.rotator import Provider, pool

NO_PROVIDERS_MESSAGE = (
    "No AI provider is configured. Add GROQ_API_KEY (and optionally "
    "CEREBRAS_API_KEY_1 / CEREBRAS_API_KEY_2) to the environment."
)


class AIUnavailableError(RuntimeError):
    """All providers failed or none are configured."""


def _headers(p: Provider) -> dict:
    return {
        "Authorization": f"Bearer {p.key}",
        "Content-Type": "application/json",
    }


def _body(p: Provider, messages: list[dict], *, model: str | None = None,
          stream: bool = False, temperature: float = 0.7,
          max_tokens: int | None = None) -> dict:
    body: dict = {
        "model": model or p.model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    return body


def _should_failover(status: int) -> bool:
    return status in (401, 403, 408, 429, 500, 502, 503, 504)


async def stream_chat(
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    model: str | None = None,
    fast: bool = False,
    exclude_keys: set[str] | None = None,
    used: list[str] | None = None,
) -> typing.AsyncIterator[str]:
    """Yield content deltas, transparently failing over across providers.
    If `used` (a list) is passed, the winning provider's key is appended."""
    tried: set[str] = set(exclude_keys or ())
    attempts = max(len(pool.providers), 1)

    for _ in range(attempts + 1):
        p = pool.acquire(exclude_keys=tried)
        if p is None:
            break
        tried.add(p.key)
        # Keyless Pollinations uses its own endpoint/module.
        if p.kind == "pollinations":
            from app.ai import keyless
            try:
                async for delta in keyless.stream_text(messages, temperature=temperature,
                                                       used=used):
                    yield delta
                return
            except keyless.KeylessError as exc:
                pool.report_failure(p, 503, str(exc))
                continue
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(config.CHAT_TIMEOUT_SECONDS, connect=10)
            ) as client:
                async with client.stream(
                    "POST",
                    f"{p.base_url}/chat/completions",
                    headers=_headers(p),
                    json=_body(p, messages, model=model,
                               stream=True, temperature=temperature,
                               max_tokens=max_tokens),
                ) as resp:
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode(errors="replace")[:300]
                        pool.report_failure(p, resp.status_code, detail)
                        if _should_failover(resp.status_code):
                            continue
                        raise AIUnavailableError(
                            f"{p.name} returned {resp.status_code}"
                        )
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            data = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
                        choices = data.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content")
                        if text:
                            yield text
            pool.report_success(p)
            if used is not None:
                used.append(p.key)
            return
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            pool.report_failure(p, "timeout", str(exc))
            continue

    raise AIUnavailableError(
        "All AI providers failed or are rate-limited. Try again shortly."
        if pool.providers else NO_PROVIDERS_MESSAGE
    )


async def complete(
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    model: str | None = None,
    fast: bool = False,
) -> str:
    """Non-streaming completion with failover. Returns the full text."""
    buf: list[str] = []
    tried: set[str] = set()
    attempts = max(len(pool.providers), 1)

    for _ in range(attempts + 1):
        p = pool.acquire(exclude_keys=tried)
        if p is None:
            break
        tried.add(p.key)
        if fast:
            model = model or p.fast_model
        # Keyless Pollinations uses its own endpoint/module.
        if p.kind == "pollinations":
            from app.ai import keyless
            try:
                text = await keyless.complete_text(messages, temperature=temperature)
                pool.report_success(p)
                return text
            except keyless.KeylessError as exc:
                pool.report_failure(p, 503, str(exc))
                continue
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(config.CHAT_TIMEOUT_SECONDS, connect=10)
            ) as client:
                resp = await client.post(
                    f"{p.base_url}/chat/completions",
                    headers=_headers(p),
                    json=_body(p, messages, model=model, stream=False,
                               temperature=temperature, max_tokens=max_tokens),
                )
            if resp.status_code == 200:
                data = resp.json()
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content")
                if text is None:
                    pool.report_failure(p, 500, f"malformed response: {resp.text[:200]}")
                    continue
                pool.report_success(p)
                return text
            pool.report_failure(p, resp.status_code, resp.text[:300])
            if not _should_failover(resp.status_code):
                raise AIUnavailableError(f"{p.name} returned {resp.status_code}")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            pool.report_failure(p, "timeout", str(exc))

    raise AIUnavailableError(
        "All AI providers failed or are rate-limited. Try again shortly."
        if pool.providers else NO_PROVIDERS_MESSAGE
    )


async def provider_status_live() -> list[dict]:
    """Ping each provider's /models endpoint to check key validity."""
    statuses = pool.status()
    for st, p in zip(statuses, pool.providers):
        if p.disabled:
            st["reachable"] = False
            continue
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{p.base_url}/models", headers=_headers(p))
            st["reachable"] = resp.status_code == 200
            if resp.status_code in (401, 403):
                pool.report_failure(p, resp.status_code, "key rejected (status check)")
                st["disabled"] = True
        except (httpx.TimeoutException, httpx.TransportError):
            st["reachable"] = False
    return statuses
