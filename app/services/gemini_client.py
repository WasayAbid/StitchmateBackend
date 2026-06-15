"""
Gemini API: map fabric images ↔ labels ↔ measurements with retries and JSON parsing.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import google.generativeai as genai
from PIL import Image

from app.config import get_settings

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


def analyze_fabric_batch(
    *,
    pieces_meta: list[dict[str, Any]],
    image_paths: list[str],
    overall: dict[str, Any] | None = None,
    overall_image_path: str | None = None,
) -> dict[str, Any]:
    """
    pieces_meta: [{ "label", "length", "width", "unit", "notes" }, ...] per image order
    Returns parsed dict with pieces[] and summary.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        return {
            "ok": False,
            "error": "GEMINI_API_KEY not configured",
            "pieces": [],
            "summary": None,
        }

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)

    prompt = """You are an expert tailoring assistant. The user uploads unstitched fabric photos for different garment parts.

For EACH fabric image (in the same order as listed below), use the user's labels and measurements when sensible, infer garment part if unclear, and note if a photo looks like a measurement card / brand tag instead of fabric.

User-provided rows (index starting at 0):
""" + json.dumps(pieces_meta, indent=2)

    if overall:
        prompt += "\n\nOptional overall measurements (user entered manually):\n" + json.dumps(overall, indent=2)

    prompt += """

Respond with ONLY valid JSON (no markdown), shape:
{
  "summary": "short natural language summary",
  "pieces": [
    {
      "index": 0,
      "user_label": "string or null",
      "suggested_label": "Shirt|Sleeves|Trouser|Dupatta|Panel|Other",
      "length": number or null,
      "width": number or null,
      "unit": "inches|meters|cm|null",
      "notes": "string",
      "confidence": "high|medium|low",
      "mapping_notes": "how measurements map to this piece"
    }
  ],
  "overall_interpretation": "string or null — how overall measurements relate to pieces if provided"
}

If an overall measurement image is included, read visible numbers and align them to pieces when possible."""

    content: list[Any] = [prompt]
    for path in image_paths:
        try:
            content.append(Image.open(path))
        except Exception as e:
            logger.warning("Skip image %s: %s", path, e)

    if overall_image_path:
        try:
            content.append(
                "Additional image below: optional overall measurement card / brand size chart. "
                "Extract numbers and map to garment pieces when possible."
            )
            content.append(Image.open(overall_image_path))
        except Exception as e:
            logger.warning("Overall image unreadable: %s", e)

    last_error: str | None = None
    for attempt in range(settings.gemini_max_retries):
        try:
            response = model.generate_content(
                content,
                generation_config=genai.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=8192,
                ),
            )
            text = (response.text or "").strip()
            if not text:
                last_error = "Empty Gemini response"
                time.sleep(1 + attempt)
                continue
            data = _extract_json(text)
            if "pieces" not in data:
                data["pieces"] = []
            data["ok"] = True
            return data
        except json.JSONDecodeError as e:
            last_error = f"JSON parse error: {e}"
            logger.warning("Gemini JSON parse attempt %s: %s", attempt + 1, last_error)
        except Exception as e:
            last_error = str(e)
            logger.warning("Gemini attempt %s failed: %s", attempt + 1, last_error)
        time.sleep(min(2 ** attempt, 8))

    return {
        "ok": False,
        "error": last_error or "Gemini failed",
        "pieces": [],
        "summary": None,
    }
