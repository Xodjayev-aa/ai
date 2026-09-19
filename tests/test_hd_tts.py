from __future__ import annotations
import asyncio, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "ai"))
import tts_hd
from tts_hd import HDTTS

class _FakeCommunicate:
    behavior = "ok"  # ok | raise | short
    calls = 0
    last_voice = ""
    last_text = ""
    def __init__(self, text, voice="v", **kw):
        _FakeCommunicate.calls += 1
        _FakeCommunicate.last_voice = voice
        _FakeCommunicate.last_text = text
    async def stream(self):
        if _FakeCommunicate.behavior == "raise":
            raise tts_hd.NoAudioReceived("boom")
        if _FakeCommunicate.behavior == "short":
            yield {"type": "audio", "data": b"x"}
            return
        yield {"type": "audio", "data": b"\x01" * 900}
        yield {"type": "WordBoundary", "data": {}}

class _FakeManager:
    voices = [
        {"ShortName": "uz-UZ-SardorNeural", "Gender": "Male", "Locale": "uz-UZ",
         "FriendlyName": "Microsoft Sardor Online (Natural) - Uzbek (Uzbekistan)"},
        {"ShortName": "uz-UZ-MadinaNeural", "Gender": "Female", "Locale": "uz-UZ",
         "FriendlyName": "Microsoft Madina Online (Natural) - Uzbek (Uzbekistan)"},
        {"ShortName": "en-GB-SoniaNeural", "Gender": "Female", "Locale": "en-GB",
         "FriendlyName": "Microsoft Sonia Online (Natural) - English (UK)"},
    ]
    @classmethod
    async def create(cls):
        return cls()

def _install_fake():
    fake = type("edge_tts", (), {})()
    fake.Communicate = _FakeCommunicate
    fake.VoicesManager = _FakeManager
    tts_hd.edge_tts = fake
    tts_hd._EDGE_AVAILABLE = True

def _fresh():
    _install_fake()
    tts = HDTTS()
    tts.breaker_cooldown = 0.0
    return tts

def test_collects_audio_chunks():
    tts = _fresh(); _FakeCommunicate.behavior = "ok"
    audio = asyncio.run(tts.synthesize("Hello there", voice="en-GB-SoniaNeural"))
    assert audio == b"\x01" * 900
    assert tts.ok == 1 and tts.failed == 0
    assert _FakeCommunicate.last_voice == "en-GB-SoniaNeural"

def test_error_returns_none_and_counts_failure():
    tts = _fresh(); _FakeCommunicate.behavior = "raise"
    assert asyncio.run(tts.synthesize("hi")) is None
    assert tts.failed == 1 and tts.ok == 0

def test_short_audio_counts_as_failure():
    tts = _fresh(); _FakeCommunicate.behavior = "short"
    assert asyncio.run(tts.synthesize("hi")) is None
    assert tts.failed == 1

def test_breaker_opens_after_threshold_then_closes():
    tts = _fresh(); tts.breaker_cooldown = 0.05
    _FakeCommunicate.behavior = "raise"
    for _ in range(tts.breaker_threshold):
        assert asyncio.run(tts.synthesize("hi")) is None
    assert tts.breaker_open() is True
    calls_before = _FakeCommunicate.calls
    assert asyncio.run(tts.synthesize("hi")) is None
    assert _FakeCommunicate.calls == calls_before  # no underlying call while open
    time.sleep(0.06)
    assert tts.breaker_open() is False
    assert asyncio.run(tts.synthesize("hi")) is None
    assert _FakeCommunicate.calls == calls_before + 1  # attempted again

def test_catalog_groups_and_picks():
    tts = _fresh()
    voices = asyncio.run(tts.voices(force=True))
    assert any(v["short"] == "uz-UZ-SardorNeural" for v in voices)
    langs = asyncio.run(tts.languages())
    assert langs[0]["code"] == "uz"
    assert tts.pick_voice("uz-UZ", "female", voices) == "uz-UZ-MadinaNeural"
    assert tts.pick_voice("uz", "male", voices) == "uz-UZ-SardorNeural"
    assert tts.pick_voice("en-GB", None, voices) == "en-GB-SoniaNeural"
    assert tts.pick_voice("xx", None, voices) == tts_hd.DEFAULT_VOICE

def test_catalog_fetch_failure_falls_back_to_popular():
    tts = _fresh()
    class _Broken:
        voices = []
        @classmethod
        async def create(cls):
            raise RuntimeError("down")
    tts_hd.edge_tts.VoicesManager = _Broken
    voices = asyncio.run(tts.voices(force=True))
    assert any(v["short"] == "uz-UZ-MadinaNeural" for v in voices)

def test_disabled_module_degrades():
    _install_fake()
    tts = HDTTS(); tts.enabled = False
    assert asyncio.run(tts.synthesize("hi")) is None
    assert asyncio.run(tts.voices(force=True))

def test_truncates_long_text():
    tts = _fresh(); _FakeCommunicate.behavior = "ok"
    asyncio.run(tts.synthesize("a" * 5000))
    assert len(_FakeCommunicate.last_text) == 2000
