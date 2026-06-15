"""Detect multiple color/size/pattern variants in accessory product images."""
from __future__ import annotations

import json
import logging
import re

import google.generativeai as genai

logger = logging.getLogger(__name__)


def detect_accessory_variants(
    accessory_image_part: dict,
    *,
    accessory_title: str,
    accessory_category: str,
) -> dict:
    """
    Analyze accessory product photo for multiple variants.

    Returns:
      {
        "has_multiple_variants": bool,
        "variants": [{"id": "1", "label": "White Lace", "description": "..."}],
        "message": str | None
      }
    """
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"""Analyze this accessory PRODUCT image for e-commerce listing "{accessory_title}" (category: {accessory_category}).

Determine if the image shows MULTIPLE distinct product variants the buyer must choose between:
- Different colors (e.g. red lace + white lace + blue lace in one photo)
- Different finishes (gold button vs silver button)
- Different patterns or sizes shown as separate options
- Multiple swatches/samples arranged as a product grid

Do NOT count multiple physical items of the SAME variant (e.g. 6 identical gold buttons in one pack) as multiple variants.

Respond ONLY with valid JSON (no markdown):
{{
  "has_multiple_variants": true or false,
  "variants": [
    {{"id": "1", "label": "Short name e.g. White Lace", "description": "brief visual description"}}
  ]
}}

If has_multiple_variants is false, return variants as a single-item array with the one accessory visible."""

    try:
        resp = model.generate_content([prompt, accessory_image_part])
        raw = re.sub(r"```json|```", "", (resp.text or "").strip()).strip()
        data = json.loads(raw)
    except Exception as exc:
        logger.warning("variant detection failed: %s", exc)
        return {
            "has_multiple_variants": False,
            "variants": [{"id": "1", "label": accessory_title, "description": accessory_category}],
            "message": None,
        }

    variants = data.get("variants") or []
    cleaned = []
    for i, v in enumerate(variants):
        if not isinstance(v, dict):
            continue
        label = (v.get("label") or "").strip()
        if not label:
            continue
        cleaned.append({
            "id": str(v.get("id") or i + 1),
            "label": label,
            "description": (v.get("description") or "").strip(),
        })

    has_multi = bool(data.get("has_multiple_variants")) and len(cleaned) > 1
    message = None
    if has_multi:
        lines = "\n".join(f"{v['id']}. {v['label']}" for v in cleaned)
        message = (
            "We detected multiple accessory variants in this product image.\n\n"
            f"Available options:\n{lines}\n\n"
            "Which variant would you like to apply to your garment?"
        )

    return {
        "has_multiple_variants": has_multi,
        "variants": cleaned if cleaned else [{"id": "1", "label": accessory_title, "description": ""}],
        "message": message,
    }
