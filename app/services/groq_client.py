"""Groq LLM helper for fabric feasibility adjustments."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _groq_key() -> Optional[str]:
    return (
        os.environ.get("GROQ_API_KEY")
        or os.environ.get("VITE_GROQ_API_KEY")
        or ""
    ).strip() or None


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


def groq_chat(prompt: str, *, temperature: float = 0.2) -> Optional[str]:
    key = _groq_key()
    if not key:
        return None
    model = os.environ.get("GROQ_MODEL") or os.environ.get("VITE_GROQ_MODEL") or DEFAULT_MODEL
    try:
        with httpx.Client(timeout=45.0) as client:
            res = client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "temperature": temperature,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        if not res.is_success:
            logger.warning("Groq error %s: %s", res.status_code, res.text[:200])
            return None
        data = res.json()
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("Groq request failed: %s", exc)
        return None


def estimate_fabric_with_groq(
    *,
    dress_type: str,
    baseline_meters: float,
    size_details: str,
) -> Optional[dict[str, Any]]:
    prompt = (
        f'Based on dress_type: "{dress_type}", baseline fabric for a medium-sized person: '
        f"{baseline_meters} meters, and user size details: {size_details}, "
        "what is the approximate adjusted minimum fabric (in meters) required to create this garment? "
        'Respond with ONLY JSON: {"minimum_fabric_meters": number, "size_label": string, "multiplier": number}'
    )
    raw = groq_chat(prompt)
    if not raw:
        return None
    try:
        parsed = _extract_json(raw)
        if "minimum_fabric_meters" in parsed:
            return parsed
    except json.JSONDecodeError:
        logger.warning("Groq fabric JSON parse failed: %s", raw[:200])
    return None


def estimate_unknown_dress_baseline_groq(dress_type: str) -> Optional[float]:
    prompt = (
        f'For Pakistani tailoring, estimate medium_size_baseline_fabric_meters for dress type: "{dress_type}". '
        'Respond ONLY JSON: {"minimum_fabric_meters": number, "reason": "one short sentence"}'
    )
    raw = groq_chat(prompt)
    if not raw:
        return None
    try:
        parsed = _extract_json(raw)
        val = parsed.get("minimum_fabric_meters")
        return float(val) if val is not None else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
