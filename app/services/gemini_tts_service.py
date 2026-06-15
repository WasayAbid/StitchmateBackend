"""Gemini text-to-speech for tailor voice guide (REST API)."""
from __future__ import annotations

import base64
import io
import logging
import os
import wave

import httpx

logger = logging.getLogger(__name__)

GEMINI_TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_TTS_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# English: Kore (clear). Urdu: Sulafat (warm) — natural friendly guide tone.
_VOICE_EN = os.environ.get("GEMINI_TTS_VOICE_EN", "Kore")
_VOICE_UR = os.environ.get("GEMINI_TTS_VOICE_UR", "Sulafat")


def _api_key() -> str:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured on the server")
    return key


def _pcm_to_wav(pcm: bytes, *, channels: int = 1, rate: int = 24000, sample_width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _urdu_tts_prompt(text: str) -> str:
    """Director-style prompt for natural Pakistani Urdu guide voice."""
    return (
        "[warm and friendly] [clear] "
        "You are a helpful StitchMate tailor shop guide speaking to Pakistani tailors. "
        "Use natural Pakistani Urdu accent and pronunciation. "
        "Speak at a natural, slightly brisk pace — not slow. "
        "Sound warm and easy to understand — like a kind shop assistant. "
        "Speak only in Urdu. Do not use English accent or Hindi accent.\n\n"
        f"{text[:2500]}"
    )


def _english_tts_prompt(text: str) -> str:
    return (
        "[clear and friendly] Read the following in English at a natural, slightly brisk pace:\n"
        f"{text[:2500]}"
    )


def text_to_speech(text: str, lang: str = "en") -> bytes:
    """Return WAV audio bytes for the given text."""
    clean = (text or "").strip()
    if not clean:
        raise ValueError("text is required")

    voice = _VOICE_UR if lang == "ur" else _VOICE_EN
    prompt = _urdu_tts_prompt(clean) if lang == "ur" else _english_tts_prompt(clean)

    model = GEMINI_TTS_MODEL.strip()
    url = f"{GEMINI_TTS_BASE}/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice},
                }
            },
        },
    }

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, params={"key": _api_key()}, json=payload)

    if resp.status_code >= 400:
        snippet = (resp.text or "")[:400]
        logger.error("Gemini TTS failed: %s %s", resp.status_code, snippet)
        resp.raise_for_status()

    data = resp.json()
    parts = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    inline = next((p.get("inlineData") for p in parts if p.get("inlineData")), None)
    if not inline or not inline.get("data"):
        raise RuntimeError("Gemini TTS returned no audio data")

    pcm = base64.b64decode(inline["data"])
    return _pcm_to_wav(pcm)
