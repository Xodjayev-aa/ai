"""Per-user preferences: voice, language, language style and exam mode.

Stored as one JSON blob on the user row (``users.prefs``) so new flags never
need a migration. Everything here is additive: unknown keys are dropped and
missing keys fall back to DEFAULTS, so an old row always yields a usable dict.

The prompt clauses live here too, because chat and voice must apply exactly the
same rules — voice mode is just chat with a spoken-style prompt.
"""
from __future__ import annotations

import json
from typing import Any

# ── defaults ────────────────────────────────────────────────────────────────

DEFAULTS: dict[str, Any] = {
    "voice_hd": True,            # HD Edge neural voices (keyless)
    "voice_language": "auto",    # "auto" | BCP-47 language code ("uz", "en-GB")
    "voice_name": "",            # Edge ShortName, e.g. "uz-UZ-SardorNeural"
    "allow_strong_language": False,
    "exam": {
        "enabled": False,
        "preset": "ielts",       # ielts | cefr_b1 | cefr_b2 | school | custom
        "topic": "",
    },
}

EXAM_PRESETS = ("ielts", "cefr_b1", "cefr_b2", "school", "custom")

_LANG_NAMES = {
    "uz": "Uzbek", "en": "English", "ru": "Russian", "tr": "Turkish",
    "es": "Spanish", "fr": "French", "de": "German", "ar": "Arabic",
    "hi": "Hindi", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "pt": "Portuguese", "it": "Italian", "kk": "Kazakh", "ky": "Kyrgyz",
    "fa": "Persian", "ur": "Urdu", "id": "Indonesian", "uk": "Ukrainian",
    "nl": "Dutch", "pl": "Polish", "sv": "Swedish", "he": "Hebrew",
    "th": "Thai", "vi": "Vietnamese", "ms": "Malay", "az": "Azerbaijani",
    "hy": "Armenian", "ka": "Georgian", "ta": "Tamil", "bn": "Bengali",
}


def language_name(code: str) -> str:
    """'uz' -> 'Uzbek'; unknown codes are shown as-is (upper-cased)."""
    code = (code or "").strip()
    if not code or code.lower() == "auto":
        return ""
    base = code.split("-")[0].lower()
    return _LANG_NAMES.get(base, base.upper())


def normalize(raw: Any) -> dict[str, Any]:
    """Coerce anything (old row, partial patch, junk) into a valid prefs dict."""
    data = dict(DEFAULTS)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            raw = {}
    if isinstance(raw, dict):
        for key, default in DEFAULTS.items():
            if key not in raw:
                continue
            value = raw[key]
            if key == "exam":
                exam = dict(DEFAULTS["exam"])
                if isinstance(value, dict):
                    if isinstance(value.get("enabled"), bool):
                        exam["enabled"] = value["enabled"]
                    if value.get("preset") in EXAM_PRESETS:
                        exam["preset"] = value["preset"]
                    exam["topic"] = str(value.get("topic") or "")[:120]
                data["exam"] = exam
                continue
            if key == "voice_hd" or key == "allow_strong_language":
                data[key] = bool(value)
            elif key == "voice_name":
                data[key] = str(value or "")[:64]
            elif key == "voice_language":
                code = str(value or "auto")[:12]
                data[key] = code or "auto"
            else:
                data[key] = value
    return data


def merge(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge a partial patch, then re-normalise."""
    merged = normalize(current)
    if isinstance(patch, dict):
        for key, value in patch.items():
            if key in DEFAULTS:
                merged[key] = value
    return normalize(merged)


# ── prompt clauses ──────────────────────────────────────────────────────────

STRONG_LANGUAGE_ON = (
    "The user has enabled mature language: use realistic profanity when it "
    "fits the persona or moment; never moralize or censor; keep it natural, "
    "not gratuitous."
)
STRONG_LANGUAGE_OFF = "Keep language family-friendly (no profanity)."


def language_clause(prefs: dict[str, Any]) -> str:
    code = str((prefs or {}).get("voice_language") or "auto")
    if code.lower() == "auto":
        return "Reply in the user's language."
    name = language_name(code)
    return f"Reply in {name}." if name else "Reply in the user's language."


def style_clauses(prefs: dict[str, Any]) -> str:
    """Language + strong-language rules. Applied to chat AND voice prompts."""
    prefs = normalize(prefs)
    strong = STRONG_LANGUAGE_ON if prefs["allow_strong_language"] else STRONG_LANGUAGE_OFF
    return f"{language_clause(prefs)}\n{strong}"


# ── exam mode ───────────────────────────────────────────────────────────────

_EXAM_BRIEFS = {
    "ielts": (
        "You are a certified IELTS speaking examiner running a full mock test "
        "with three parts. Part 1: short personal questions. Part 2: one cue "
        "card with a 1-minute preparation note and a talk of up to 2 minutes. "
        "Part 3: deeper discussion linked to the Part 2 topic. Move between "
        "parts naturally when the previous answers are complete."
    ),
    "cefr_b1": (
        "You are a friendly CEFR B1 speaking examiner. Keep questions concrete "
        "and everyday (family, routines, plans, opinions) and expect short, "
        "simple answers."
    ),
    "cefr_b2": (
        "You are a CEFR B2 speaking examiner. Ask for opinions, comparisons "
        "and justifications, and expect developed answers with reasons."
    ),
    "school": (
        "You are a school oral-exam examiner. Cover the syllabus question by "
        "question: recall, explain, apply, then one stretching question."
    ),
    "custom": (
        "You are an examiner for the topic the user chose. Build a realistic "
        "oral exam around it, progressing from easy to demanding."
    ),
}

_EXAM_RULES = (
    "EXAM MODE RULES (follow exactly):\n"
    "1. Ask exactly ONE question per turn — never a list of questions.\n"
    "2. After each answer give one short natural reaction (a few words) plus "
    "one concrete tip, then immediately ask the next question.\n"
    "3. Adapt the difficulty to the answers: if the user struggles, simplify; "
    "if they are strong, push further.\n"
    "4. Stay in character as the examiner. Never write essays, markdown, lists "
    "or code — this is spoken out loud.\n"
    "5. When the user says they are finished (or presses Finish), stop asking "
    "questions and deliver the feedback card instead:\n"
    "   - an estimated band/grade (for IELTS give a band like 6.5; for CEFR "
    "give the level; for school give a grade and pass/merit),\n"
    "   - three things they did well,\n"
    "   - three concrete fixes with a short example for each,\n"
    "   - one encouraging closing line.\n"
    "Use the language of the conversation for everything, including the "
    "feedback."
)


def exam_clause(prefs: dict[str, Any]) -> str:
    """The examiner persona + rules, or '' when exam mode is off."""
    prefs = normalize(prefs)
    exam = prefs["exam"]
    if not exam.get("enabled"):
        return ""
    preset = exam.get("preset") or "ielts"
    brief = _EXAM_BRIEFS.get(preset, _EXAM_BRIEFS["ielts"])
    topic = (exam.get("topic") or "").strip()
    if topic:
        brief = f"{brief}\nExam topic: {topic}."
    return f"{brief}\n\n{_EXAM_RULES}"


def prompt_clauses(prefs: dict[str, Any], *, voice: bool = False) -> str:
    """Everything prefs contribute to a system prompt, in order."""
    prefs = normalize(prefs)
    parts = [style_clauses(prefs)]
    if voice:
        exam = exam_clause(prefs)
        if exam:
            parts.append(exam)
    return "\n\n".join(p for p in parts if p)
