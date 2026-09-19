"""Provider pool with round-robin rotation and cooldown/failover.

Strategy for "multiple people, free tiers":
  - Requests rotate across every configured key (Cerebras key 1,
    Cerebras key 2, Groq) so load is spread.
  - On 429 (rate limit) a key cools down for COOLDOWN_SECONDS and the
    next key takes over automatically.
  - On 401/403 (invalid key) the key is disabled until process restart.
"""

import itertools
import threading
import time
from dataclasses import dataclass, field

from app.ai import config

COOLDOWN_SECONDS = 65.0  # just over a minute; covers per-minute rate limits


@dataclass
class Provider:
    name: str
    kind: str
    base_url: str
    key: str
    model: str
    fast_model: str
    cooldown_until: float = 0.0
    disabled: bool = False
    ok_count: int = 0
    fail_count: int = 0
    last_error: str = ""
    _cycle: "itertools.count" = field(default_factory=itertools.count)


class Pool:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._providers: list[Provider] = []
        self._rr = 0
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._providers = [
                Provider(**p) for p in config.configured_providers()
            ]
            self._rr = 0

    @property
    def providers(self) -> list[Provider]:
        return self._providers

    def acquire(self, exclude_keys: set[str] | None = None) -> Provider | None:
        exclude = exclude_keys or set()
        with self._lock:
            n = len(self._providers)
            if n == 0:
                return None
            now = time.time()
            # Prefer a non-excluded, healthy provider; fall back to any
            # healthy provider (even excluded) only if nothing else exists.
            fallback = None
            for offset in range(n):
                idx = (self._rr + offset) % n
                p = self._providers[idx]
                if p.disabled or p.cooldown_until > now:
                    continue
                if p.key in exclude:
                    fallback = fallback or p
                    continue
                self._rr = (idx + 1) % n
                return p
            # Everything healthy is excluded or cooling: retry cooled ones.
            for offset in range(n):
                idx = (self._rr + offset) % n
                p = self._providers[idx]
                if not p.disabled and p.key not in exclude:
                    self._rr = (idx + 1) % n
                    return p
            return fallback

    def report_success(self, p: Provider) -> None:
        with self._lock:
            p.ok_count += 1
            p.cooldown_until = 0.0
            p.last_error = ""

    def report_failure(self, p: Provider, status: int | str, detail: str = "") -> None:
        with self._lock:
            p.fail_count += 1
            p.last_error = f"{status}: {detail[:180]}"
            if status in (401, 403):
                p.disabled = True  # bad key — stop using it entirely
            elif status == 429 or status == "timeout":
                p.cooldown_until = time.time() + COOLDOWN_SECONDS

    def status(self) -> list[dict]:
        now = time.time()
        with self._lock:
            return [{
                "name": p.name,
                "kind": p.kind,
                "model": p.model,
                "key": config.key_label(p.key),
                "disabled": p.disabled,
                "cooling_down": p.cooldown_until > now,
                "ok": p.ok_count,
                "failed": p.fail_count,
                "last_error": p.last_error,
            } for p in self._providers]


pool = Pool()
