"""Pacing, queueing and cooldowns for the free keyless tier.

Why this exists
---------------
The keyless Pollinations free tier is rate-limited per *server IP*, and every
serverless instance of Aether shares the same egress IP. If ten people ask a
question at the same moment, nine of them get a 429 and a broken answer.

So keyless requests go through one process-wide pacer:

* spaced out by a minimum interval (self-tuning, see below),
* queued instead of rejected — a request may wait, but it may not hammer,
* cooled down explicitly after a 429, with the wait surfaced to the UI,
* bounded: if the queue wait would exceed ``max_wait`` we raise
  :class:`PacerBusy` so the caller can show an honest "cooling down, retry in
  Ns" message instead of a hung request.

The interval adapts: every 429 doubles it (up to ``max_interval``), and it
decays slowly back towards ``min_interval`` after successes. That means the
app *learns* how strict the free tier is right now instead of assuming.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections import deque

__all__ = ["PacerBusy", "KeylessPacer", "pacer"]


def _env_float(name: str, default: float) -> float:
    try:
        value = float((os.getenv(name) or "").strip())
        return value if value > 0 else default
    except ValueError:
        return default


class PacerBusy(RuntimeError):
    """The free tier is cooling down / the queue is too long right now."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = max(1.0, float(retry_after))


class KeylessPacer:
    def __init__(
        self,
        *,
        min_interval: float | None = None,
        max_interval: float | None = None,
        max_wait: float | None = None,
    ) -> None:
        self.min_interval = min_interval if min_interval is not None else _env_float(
            "KEYLESS_MIN_INTERVAL", 5.0)
        self.max_interval = max_interval if max_interval is not None else _env_float(
            "KEYLESS_MAX_INTERVAL", 45.0)
        self.max_wait = max_wait if max_wait is not None else _env_float(
            "KEYLESS_MAX_WAIT", 22.0)
        self.interval = self.min_interval

        self._lock = asyncio.Lock()
        self._next_allowed = 0.0
        self._cooldown_until = 0.0
        self._waiting = 0
        self._recent: deque[float] = deque(maxlen=64)
        self.stats = {
            "granted": 0,
            "queued": 0,
            "rate_limited": 0,
            "waited_seconds": 0.0,
            "last_wait": 0.0,
            "succeeded": 0,
            "failed": 0,
        }

    # ------------------------------------------------------------------ state
    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.time())

    def estimated_wait(self) -> float:
        """Roughly how long the next request would sit in the queue."""
        now = time.time()
        return max(self.cooldown_remaining(),
                   max(0.0, self._next_allowed - now),
                   (self._waiting * self.interval) if self._waiting else 0.0)

    def snapshot(self) -> dict:
        now = time.time()
        while self._recent and now - self._recent[0] > 60:
            self._recent.popleft()
        cooldown = self.cooldown_remaining()
        return {
            "interval_seconds": round(self.interval, 1),
            "min_interval_seconds": round(self.min_interval, 1),
            "max_interval_seconds": round(self.max_interval, 1),
            "queued_requests": self._waiting,
            "requests_last_minute": len(self._recent),
            "cooling_down": cooldown > 0,
            "retry_after_seconds": round(cooldown, 1),
            "estimated_wait_seconds": round(self.estimated_wait(), 1),
            **{k: (round(v, 1) if isinstance(v, float) else v)
               for k, v in self.stats.items()},
        }

    # ---------------------------------------------------------------- control
    def rate_limited(self, retry_after: float | None = None) -> None:
        """A 429 (or equivalent) came back: back off aggressively."""
        self.stats["rate_limited"] += 1
        wait = retry_after if retry_after and retry_after > 0 else None
        self.interval = min(self.max_interval, max(self.interval * 2, self.min_interval))
        cooldown = max(wait or 0.0, min(self.max_interval, self.interval * 2))
        self._cooldown_until = max(self._cooldown_until, time.time() + cooldown)
        self._next_allowed = max(self._next_allowed, self._cooldown_until)

    def success(self) -> None:
        self.stats["succeeded"] += 1
        self._cooldown_until = 0.0
        if self.interval > self.min_interval:
            # Decay gently so a single good answer does not undo the lesson.
            self.interval = max(self.min_interval, self.interval * 0.85)

    def failure(self) -> None:
        self.stats["failed"] += 1

    # ------------------------------------------------------------------ queue
    async def acquire(self) -> float:
        """Wait for a slot. Returns how long we waited (seconds).

        Raises :class:`PacerBusy` when the wait would be unreasonably long —
        callers translate that into a friendly, countable cooldown message.
        """
        self._waiting += 1
        self.stats["queued"] += 1
        started = time.time()
        try:
            while True:
                async with self._lock:
                    now = time.time()
                    elapsed = now - started
                    wait = max(0.0, self._next_allowed - now)
                    if self._cooldown_until > now:
                        wait = max(wait, self._cooldown_until - now)
                    if wait <= 0.05:
                        self._next_allowed = now + self.interval
                        self._recent.append(now)
                        self.stats["granted"] += 1
                        self.stats["waited_seconds"] += elapsed
                        self.stats["last_wait"] = round(elapsed, 2)
                        return elapsed
                    if wait > self.max_wait and elapsed > 1.0:
                        raise PacerBusy(
                            "Free AI is busy right now — retry shortly.",
                            wait,
                        )
                sleep_for = min(wait, 1.0) + random.uniform(0.0, 0.25)
                await asyncio.sleep(sleep_for)
        finally:
            self._waiting = max(0, self._waiting - 1)


# One pacer per process. Vercel may run several instances; each instance still
# self-limits, and the UI shows an honest queue estimate either way.
pacer = KeylessPacer()
