"""ElevenLabs text-to-speech for tailor voice guide."""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "eleven_turbo_v2_5"
MULTILINGUAL_MODEL = "eleven_multilingual_v2"
_voice_cache: dict[str, str] = {}


def _api_key() -> str:
    key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not configured on the server")
    return key


def _headers() -> dict[str, str]:
    return {"xi-api-key": _api_key(), "Content-Type": "application/json"}


def list_account_voices() -> list[dict]:
    """Return voices available on this ElevenLabs account (works on free tier)."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"{ELEVENLABS_BASE}/voices", headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    voices = data.get("voices") if isinstance(data, dict) else None
    return voices if isinstance(voices, list) else []


def resolve_voice_id(lang: str) -> str:
    """Pick a voice ID that works on the user's plan."""
    env_key = "ELEVENLABS_VOICE_ID_UR" if lang == "ur" else "ELEVENLABS_VOICE_ID_EN"
    env_voice = (os.environ.get(env_key) or os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
    if env_voice:
        return env_voice

    cache_key = lang
    if cache_key in _voice_cache:
        return _voice_cache[cache_key]

    try:
        voices = list_account_voices()
        if voices:
            # Prefer multilingual / premade voices on the account
            preferred = None
            for v in voices:
                vid = str(v.get("voice_id") or "")
                name = str(v.get("name") or "").lower()
                if not vid:
                    continue
                if lang == "ur" and ("urdu" in name or "multilingual" in name):
                    preferred = vid
                    break
                if "multilingual" in name or "turbo" in name or v.get("category") == "premade":
                    preferred = vid
                    break
            voice_id = preferred or str(voices[0].get("voice_id") or "")
            if voice_id:
                _voice_cache[cache_key] = voice_id
                logger.info("ElevenLabs using account voice %s for lang=%s", voice_id, lang)
                return voice_id
    except Exception as e:
        logger.warning("Could not list ElevenLabs voices: %s", e)

    raise RuntimeError(
        "No usable ElevenLabs voice on your account. "
        "Create a voice in ElevenLabs dashboard or set ELEVENLABS_VOICE_ID in .env"
    )


def _model_for_lang(lang: str) -> str:
    if lang == "ur":
        return (
            os.environ.get("ELEVENLABS_MODEL_ID_UR")
            or os.environ.get("ELEVENLABS_MODEL_ID")
            or MULTILINGUAL_MODEL
        ).strip()
    return (os.environ.get("ELEVENLABS_MODEL_ID") or DEFAULT_MODEL).strip()


def text_to_speech(text: str, lang: str = "en") -> bytes:
    voice_id = resolve_voice_id(lang)
    model_id = _model_for_lang(lang)
    clean = (text or "").strip()
    if not clean:
        raise ValueError("text is required")

    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}"
    headers = {**_headers(), "Accept": "audio/mpeg"}
    payload: dict = {
        "text": clean[:2500],
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.72,
            "similarity_boost": 0.82,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }
    # turbo models reject language_code=ur — multilingual v2 reads Urdu script from text
    if lang == "ur" and "multilingual" not in model_id.lower():
        payload["model_id"] = MULTILINGUAL_MODEL

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            logger.error("ElevenLabs TTS failed: %s %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        return resp.content
