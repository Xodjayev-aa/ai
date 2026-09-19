"""HD neural TTS for Aether — Edge read-aloud voices (free, keyless, ~140 languages).

Dependency: the `edge-tts` package (pin >=7.2.8). Must run server-side: the
websocket needs custom headers browsers are not allowed to set.

Reliability contract (unofficial service; changed Apr 2025 and Dec 2025, pip
package tracked both within days): every call has a hard timeout, a circuit
breaker opens after N consecutive failures, and callers must treat None as
"fall back" (legacy keyless TTS, then 503 {browser_tts:true}).
Callers send sentence-length text; a single call is capped at 2000 chars.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

try:
    import edge_tts  # type: ignore
    from edge_tts.exceptions import NoAudioReceived, UnexpectedResponse, UnknownResponse  # type: ignore
    _EDGE_AVAILABLE = True
except Exception:
    edge_tts = None  # type: ignore[assignment]
    NoAudioReceived = UnexpectedResponse = UnknownResponse = Exception  # type: ignore
    _EDGE_AVAILABLE = False

CATALOG_TTL = 3600.0
CALL_TIMEOUT = 25.0
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN = 180.0
DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"

POPULAR = [
    ("uz-UZ-SardorNeural", "Sardor — Uzbek (male)"),
    ("uz-UZ-MadinaNeural", "Madina — Uzbek (female)"),
    ("en-US-AndrewMultilingualNeural", "Andrew — English US, multilingual (male)"),
    ("en-US-EmmaMultilingualNeural", "Emma — English US, multilingual (female)"),
    ("en-GB-RyanNeural", "Ryan — English UK (male)"),
    ("en-GB-SoniaNeural", "Sonia — English UK (female)"),
    ("ru-RU-DmitryNeural", "Dmitry — Russian (male)"),
    ("ru-RU-SvetlanaNeural", "Svetlana — Russian (female)"),
    ("tr-TR-AhmetNeural", "Ahmet — Turkish (male)"),
    ("tr-TR-EmelNeural", "Emel — Turkish (female)"),
]

_LANG_NAMES = {
    "uz": "Uzbek", "en": "English", "ru": "Russian", "tr": "Turkish",
    "es": "Spanish", "fr": "French", "de": "German", "ar": "Arabic",
    "hi": "Hindi", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "pt": "Portuguese", "it": "Italian", "kk": "Kazakh", "ky": "Kyrgyz",
    "fa": "Persian", "ur": "Urdu", "id": "Indonesian", "uk": "Ukrainian",
}

def _mono() -> float:
    return time.monotonic()

class HDTTS:
    def __init__(self) -> None:
        self.enabled = _EDGE_AVAILABLE
        self.call_timeout = CALL_TIMEOUT
        self.breaker_threshold = BREAKER_THRESHOLD
        self.breaker_cooldown = BREAKER_COOLDOWN
        self._catalog: Optional[list[dict[str, Any]]] = None
        self._catalog_at = 0.0
        self._fail_streak = 0
        self._open_until = 0.0
        self.ok = 0
        self.failed = 0

    async def voices(self, force: bool = False) -> list[dict[str, Any]]:
        """Live catalog as [{short, name, locale, lang, gender}]; cached 1h;
        falls back to POPULAR if the fetch fails."""
        if not force and self._catalog and _mono() - self._catalog_at < CATALOG_TTL:
            return self._catalog
        items: list[dict[str, Any]] = []
        if self.enabled:
            try:
                manager = await asyncio.wait_for(edge_tts.VoicesManager.create(), timeout=15.0)
                for v in manager.voices:
                    items.append({
                        "short": v["ShortName"],
                        "name": v.get("FriendlyName", v["ShortName"]).replace("Microsoft ", "").replace(" Online (Natural)", ""),
                        "locale": v.get("Locale", ""),
                        "lang": str(v.get("Locale", "")).split("-")[0].lower(),
                        "gender": str(v.get("Gender", "")).lower(),
                    })
            except Exception:
                items = []
        if not items:
            items = [{"short": s, "name": n,
                      "locale": s.rsplit("-", 1)[0] if "-" in s else s,
                      "lang": s.split("-")[0].lower(),
                      "gender": "female" if "madina" in s.lower() or "female" in n.lower() else "male"}
                     for s, n in POPULAR]
        self._catalog = items
        self._catalog_at = _mono()
        return items

    async def languages(self) -> list[dict[str, Any]]:
        """Grouped language list for the Settings dropdown, popular first."""
        voices = await self.voices()
        counts: dict[str, int] = {}
        for v in voices:
            counts[v["lang"]] = counts.get(v["lang"], 0) + 1
        out = [{"code": c, "name": _LANG_NAMES.get(c, c.upper()), "count": n} for c, n in counts.items()]
        prio = {"uz": 0, "en": 1, "ru": 2, "tr": 3}
        out.sort(key=lambda l: (prio.get(l["code"], 10), l["name"]))
        return out

    def pick_voice(self, lang: str, gender: Optional[str] = None,
                   catalog: Optional[list[dict[str, Any]]] = None) -> str:
        """Best voice for a BCP-47 tag like 'uz-UZ' or 'uz'. Exact locale ->
        same language -> multilingual default."""
        catalog = catalog or []
        want = (lang or "en").lower()
        exact = [v for v in catalog if v["locale"].lower() == want]
        pool = exact or [v for v in catalog if v["lang"] == want.split("-")[0]]
        if pool:
            if gender:
                by_g = [v for v in pool if v["gender"] == gender.lower()]
                if by_g:
                    return by_g[0]["short"]
            return pool[0]["short"]
        return DEFAULT_VOICE

    def breaker_open(self) -> bool:
        return _mono() < self._open_until

    def _register_failure(self) -> None:
        self.failed += 1
        self._fail_streak += 1
        if self._fail_streak >= self.breaker_threshold:
            self._open_until = _mono() + self.breaker_cooldown
            self._fail_streak = 0

    async def synthesize(self, text: str, voice: str = DEFAULT_VOICE,
                         rate: str = "+0%", pitch: str = "+0Hz") -> Optional[bytes]:
        """Return mp3 bytes, or None when the caller should fall back."""
        if not self.enabled or not text or not text.strip():
            return None
        if self.breaker_open():
            return None
        try:
            com = edge_tts.Communicate(text[:2000], voice=voice or DEFAULT_VOICE,
                                       rate=rate, pitch=pitch,
                                       receive_timeout=int(self.call_timeout))
            chunks: list[bytes] = []
            async def _run() -> None:
                async for chunk in com.stream():
                    if chunk.get("type") == "audio":
                        chunks.append(chunk["data"])
            await asyncio.wait_for(_run(), timeout=self.call_timeout)
        except Exception:
            self._register_failure()
            return None
        audio = b"".join(chunks)
        if len(audio) < 512:
            self._register_failure()
            return None
        self.ok += 1
        self._fail_streak = 0
        return audio

    def status(self) -> dict[str, Any]:
        return {"provider": "edge-neural", "enabled": bool(self.enabled),
                "voices_cached": len(self._catalog or []), "ok": self.ok,
                "failed": self.failed, "breaker_open": self.breaker_open()}
