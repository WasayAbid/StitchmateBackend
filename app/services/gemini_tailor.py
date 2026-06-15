"""
Gemini prompts: expert Pakistani tailor & fashion designer — feasibility + design spec JSON.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import google.generativeai as genai

from app.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_CONTEXT = """You are an expert Pakistani fashion designer and master tailor with decades of experience
in shalwar kameez, kurta, lehenga, frock, dupatta draping, gents suits, and unstitched fabric layout.
You work in inches and meters, understand standard Pakistani retail fabric widths (often 36–60 inches),
and give practical cutting / stitching guidance."""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


def _call_model(prompt: str, max_retries: int = 3) -> dict[str, Any]:
    settings = get_settings()
    if not settings.gemini_api_key:
        return {"ok": False, "error": "GEMINI_API_KEY not configured"}

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)

    last = "unknown"
    for attempt in range(max_retries):
        try:
            response = model.generate_content(
                [SYSTEM_CONTEXT, prompt],
                generation_config=genai.GenerationConfig(temperature=0.25, max_output_tokens=8192),
            )
            text = (response.text or "").strip()
            if not text:
                last = "empty response"
                time.sleep(1 + attempt)
                continue
            data = _extract_json(text)
            data["ok"] = True
            return data
        except json.JSONDecodeError as e:
            last = f"json: {e}"
            logger.warning("Gemini JSON parse: %s", last)
        except Exception as e:
            last = str(e)
            logger.warning("Gemini attempt %s: %s", attempt + 1, last)
        time.sleep(min(2 ** attempt, 8))

    return {"ok": False, "error": last}


def run_feasibility_analysis(
    *,
    fabric_pieces: list[dict[str, Any]],
    fabric_batch_overall: dict[str, Any] | None,
    design: dict[str, Any],
) -> dict[str, Any]:
    """
    design keys: prompt_text, builtin_labels (list str), neckline, has_reference_image (bool)
    """
    prompt = f"""{SYSTEM_CONTEXT}

You have the user's fabric pieces (labels, measurements, notes):
{json.dumps(fabric_pieces, indent=2)}

Optional overall fabric / measurement context:
{json.dumps(fabric_batch_overall or {}, indent=2)}

User design preferences:
- Text prompt: {design.get("prompt_text") or "(none)"}
- Selected built-in garment / style options (may be multiple): {json.dumps(design.get("builtin_labels") or [])}
- Neckline preference: {design.get("neckline") or "(none)"}
- User uploaded a reference design image: {bool(design.get("has_reference_image"))}

Respond with ONLY valid JSON (no markdown), shape:
{{
  "feasible": true or false,
  "confidence": "high|medium|low",
  "summary": "2-4 sentences for the customer in clear English",
  "reasoning": "technical reasoning for tailors",
  "fabric_to_garment_mapping": [
    {{ "fabric_label": "string", "used_for": "e.g. Kameez front / sleeves", "notes": "string" }}
  ],
  "if_not_feasible": {{
    "suggestions": ["string", "..."],
    "minimum_extra_fabric": "string or null"
  }},
  "tailoring_plan_steps": [
    "Step 1: ...",
    "Step 2: ..."
  ],
  "measurement_checks": [
    {{ "piece": "string", "check": "string" }}
  ]
}}
"""
    return _call_model(prompt)


def run_design_generation_spec(
    *,
    feasibility_result: dict[str, Any],
    fabric_pieces: list[dict[str, Any]],
    design: dict[str, Any],
) -> dict[str, Any]:
    """Rich visual specification for UI preview (not a bitmap)."""
    prompt = f"""{SYSTEM_CONTEXT}

Feasibility analysis (already done):
{json.dumps(feasibility_result, indent=2)[:12000]}

Fabrics:
{json.dumps(fabric_pieces, indent=2)[:8000]}

Design prefs:
{json.dumps(design, indent=2)}

Produce a visual design specification for the outfit. Respond with ONLY valid JSON:
{{
  "garment_title": "string",
  "silhouette": "string",
  "primary_colors": ["#RRGGBB", "..."],
  "accent_colors": ["#RRGGBB"],
  "fabric_usage_description": "string",
  "key_style_elements": ["string", "..."],
  "neckline_description": "string",
  "embroidery_or_trim_notes": "string",
  "customer_facing_summary": "2-3 sentences describing how the finished outfit will look"
}}
"""
    return _call_model(prompt)


def check_feasibility_simple(
    *,
    fabric_pieces: list[dict[str, Any]],
    design_description: str,
    builtin_design_descriptions: list[str],
    neckline: str | None,
) -> dict[str, Any]:
    """
    Strict Yes/No feasibility check.
    Returns { "feasible": bool, "reason": str, "ok": bool }.
    Gemini is instructed to respond with ONLY a JSON object — no extra text.
    """
    fabric_summary = "\n".join(
        f"- {p.get('label') or 'Fabric'}: "
        f"{p.get('length') or '?'} × {p.get('width') or '?'} {p.get('unit') or 'inches'}"
        + (f" | notes: {p['notes']}" if p.get("notes") else "")
        for p in fabric_pieces
    ) or "No fabric details provided."

    design_parts: list[str] = []
    if design_description.strip():
        design_parts.append(f"Customer description: {design_description.strip()}")
    if builtin_design_descriptions:
        design_parts.append("Selected garment styles:\n" + "\n".join(
            f"  • {d}" for d in builtin_design_descriptions
        ))
    if neckline:
        design_parts.append(f"Neckline preference: {neckline}")
    design_block = "\n".join(design_parts) or "No specific design described."

    prompt = f"""{SYSTEM_CONTEXT}

A customer has the following fabric:
{fabric_summary}

They want to make:
{design_block}

Based ONLY on the fabric quantity, dimensions, and the garment's typical fabric requirements,
is it physically feasible to make this garment?

You MUST respond with ONLY this JSON — no explanation, no markdown, no extra text:
{{"feasible": true, "reason": "One sentence explaining why it is feasible."}}
or
{{"feasible": false, "reason": "One sentence explaining why it is not feasible and what is missing."}}
"""
    return _call_model(prompt)
