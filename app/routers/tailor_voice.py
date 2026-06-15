"""Tailor voice guide — TTS proxy (Gemini → ElevenLabs fallback)."""
from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tailor-voice", tags=["tailor-voice"])


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2500)
    lang: str = Field(default="en", pattern="^(en|ur)$")


class SpeakResponse(BaseModel):
    audio_base64: str
    content_type: str = "audio/wav"
    provider: str = "gemini"


def _synthesize(text: str, lang: str) -> tuple[bytes, str, str]:
    """Urdu prefers ElevenLabs multilingual; English tries Gemini first."""
    errors: list[str] = []
    providers = ["elevenlabs", "gemini"] if lang == "ur" else ["gemini", "elevenlabs"]

    for provider in providers:
        try:
            if provider == "gemini":
                from app.services.gemini_tts_service import text_to_speech as gemini_tts

                audio = gemini_tts(text, lang)
                return audio, "audio/wav", "gemini"
            from app.services.elevenlabs_service import text_to_speech as eleven_tts

            audio = eleven_tts(text, lang)
            return audio, "audio/mpeg", "elevenlabs"
        except Exception as e:
            errors.append(f"{provider}: {e}")
            logger.warning("%s TTS unavailable for lang=%s: %s", provider, lang, e)

    logger.error("All TTS providers failed: %s", errors)
    raise RuntimeError("; ".join(errors))


@router.post("/speak", response_model=SpeakResponse)
def speak(req: SpeakRequest):
    try:
        audio, content_type, provider = _synthesize(req.text, req.lang)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("TTS error")
        detail = str(e) if str(e) else "Text-to-speech failed"
        raise HTTPException(status_code=502, detail=detail) from e

    return SpeakResponse(
        audio_base64=base64.b64encode(audio).decode("ascii"),
        content_type=content_type,
        provider=provider,
    )
