"""Gemini image generation via REST API.

The deprecated ``google.generativeai`` SDK does not support ``responseModalities``
on ``GenerationConfig``; image output requires the v1beta REST endpoint.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")


def _to_api_part(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        return {"text": item}
    if isinstance(item, dict) and "inline_data" in item:
        inline = item["inline_data"]
        return {
            "inlineData": {
                "mimeType": inline.get("mime_type", "image/jpeg"),
                "data": inline["data"],
            }
        }
    raise TypeError(f"Unsupported render part type: {type(item)!r}")


def generate_image_from_parts(
    parts: list[Any],
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout_s: int = 90,
) -> str:
    """Return a data-URL for the generated image."""
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    model_name = model or GEMINI_IMAGE_MODEL
    api_parts = [_to_api_part(p) for p in parts]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

    payload = {
        "contents": [{"parts": api_parts}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }

    with httpx.Client(timeout=timeout_s) as client:
        res = client.post(url, params={"key": key}, json=payload)

    if not res.is_success:
        snippet = (res.text or "")[:400]
        logger.error("Gemini image API %s: %s", res.status_code, snippet)
        raise RuntimeError(f"Gemini Image API error {res.status_code}: {snippet}")

    data = res.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates")

    for part in candidates[0].get("content", {}).get("parts", []):
        inline = part.get("inlineData") or {}
        b64 = inline.get("data")
        if b64:
            mime = inline.get("mimeType", "image/png")
            return f"data:{mime};base64,{b64}"

    raise RuntimeError("Gemini did not return an image")
