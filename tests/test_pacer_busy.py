"""Regression: KeylessPacer.acquire must never raise UnboundLocalError.

Production symptom behind this test: under load the keyless pacer cooled
down, and the very first acquire() hit the busy check
``if wait > self.max_wait and elapsed > 1.0`` with ``elapsed`` not yet
assigned (it was only set on the grant path). Every affected request 500'd
with ``UnboundLocalError: cannot access local variable 'elapsed'`` instead
of the honest PacerBusy "cooling down" response.

    .venv/bin/python -m pytest tests/test_pacer_busy.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.queue import KeylessPacer, PacerBusy  # noqa: E402


def test_cold_cooldown_raises_pacer_busy_not_unbound_local():
    """First-ever acquire() while the pacer is in a long cooldown: the old
    code died with UnboundLocalError before ever reaching PacerBusy."""
    p = KeylessPacer(min_interval=0.01, max_interval=0.02, max_wait=0.2)
    p.rate_limited(retry_after=30)  # long cooldown, tiny max_wait

    async def run() -> PacerBusy:
        with pytest.raises(PacerBusy) as excinfo:
            await p.acquire()
        return excinfo.value

    err = asyncio.run(run())
    assert err.retry_after >= 1.0
    assert p.stats["queued"] == 1
    assert p.stats["granted"] == 0, "nothing may be granted during cooldown"


def test_acquire_grants_immediately_when_idle():
    p = KeylessPacer(min_interval=0.01, max_interval=0.02, max_wait=5.0)
    waited = asyncio.run(p.acquire())
    assert waited >= 0.0
    assert p.stats["granted"] == 1
    assert p.stats["last_wait"] == round(waited, 2)


def test_acquire_waits_out_a_short_queue():
    """A wait below max_wait must be sat out and granted, not rejected."""
    p = KeylessPacer(min_interval=0.05, max_interval=0.05, max_wait=5.0)
    p._next_allowed = time.time() + 0.3
    waited = asyncio.run(p.acquire())
    assert waited >= 0.25
    assert p.stats["granted"] == 1
