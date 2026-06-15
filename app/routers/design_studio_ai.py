"""Design Studio AI: dynamic fabric feasibility + generate-dress orchestration."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any, Optional

import google.generativeai as genai
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.fabric_feasibility import (
    build_reference_gemini_prompt,
    run_feasibility_analysis,
)
from app.services.fabric_baselines import list_baseline_labels, lookup_baseline

logger = logging.getLogger(__name__)

studio_ai_router = APIRouter(prefix="/ai", tags=["design-studio"])

genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))


class FabricPieceIn(BaseModel):
    label: Optional[str] = None
    length: Optional[float] = None
    width: Optional[float] = None
    unit: Optional[str] = "inches"
    notes: Optional[str] = None


class FeasibilityAnalysisRequest(BaseModel):
    fabric_pieces: list[FabricPieceIn] = Field(default_factory=list)
    design_description: str = ""
    selected_design_ids: list[str] = Field(default_factory=list)
    selected_design_names: list[str] = Field(default_factory=list)
    reference_image_url: Optional[str] = None
    neckline: Optional[str] = None
    meas_card_text: Optional[str] = None


class GenerateDressRequest(FeasibilityAnalysisRequest):
    user_prompt: Optional[str] = None
    user_specified_fabric: Optional[str] = None
    user_specified_color: Optional[str] = None
    user_specified_length: Optional[str] = None


def _img_part(url: str) -> dict:
    if url.startswith("data:"):
        header, b64data = url.split(",", 1)
        mime = "image/jpeg"
        if ":" in header:
            mime = header.split(":")[1].split(";")[0] or mime
        return {"inline_data": {"mime_type": mime, "data": b64data}}
    data = httpx.get(url, timeout=25, follow_redirects=True).content
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(data).decode()}}


async def identify_dress_type_from_reference(reference_image_url: str) -> str:
    """Gemini Vision: silhouette / dress type only — ignore fabric, color, embroidery."""
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = (
        "Analyze this fashion reference image. Identify ONLY the garment silhouette / dress type "
        "(e.g. Anarkali frock, Shalwar Kameez, Frock with sharara, Plain kurti). "
        "IGNORE fabric material, color, embroidery, lace, beads, and accessories. "
        'Respond with ONLY JSON: {"dress_type": "short label"}'
    )
    try:
        resp = model.generate_content([prompt, _img_part(reference_image_url)])
        raw = re.sub(r"```json|```", "", (resp.text or "").strip()).strip()
        data = json.loads(raw)
        return str(data.get("dress_type") or "Custom dress").strip()
    except Exception as exc:
        logger.warning("Reference dress type identification failed: %s", exc)
        return "Custom dress"


@studio_ai_router.get("/fabric-baselines")
async def get_fabric_baselines():
    """Medium-size baseline fabric lookup (meters) for built-in dress types."""
    from app.services.fabric_baselines import FABRIC_BASELINES

    return {
        "note": "medium_size_baseline_fabric_meters — foundation for dynamic size adjustments",
        "baselines": [
            {
                "key": key,
                "dress_type": b.dress_type,
                "min_meters": b.min_meters,
                "max_meters": b.max_meters,
                "medium_baseline_meters": b.medium_baseline_meters,
            }
            for key, b in sorted(FABRIC_BASELINES.items(), key=lambda x: x[1].dress_type)
        ],
    }


@studio_ai_router.post("/feasibility-analysis")
async def feasibility_analysis(body: FeasibilityAnalysisRequest):
    dress_type_ref: Optional[str] = None
    if body.reference_image_url:
        dress_type_ref = await identify_dress_type_from_reference(body.reference_image_url)

    result = run_feasibility_analysis(
        fabric_pieces=[p.model_dump() for p in body.fabric_pieces],
        design_description=body.design_description.strip(),
        selected_design_ids=body.selected_design_ids,
        selected_design_names=body.selected_design_names or None,
        dress_type_from_reference=dress_type_ref,
        meas_card_text=body.meas_card_text,
    )
    if dress_type_ref:
        result["feasibility_analysis"]["reference_dress_type"] = dress_type_ref
    return result


@studio_ai_router.post("/generate-dress")
async def generate_dress(body: GenerateDressRequest):
    """
    Dynamic feasibility analysis + Gemini generation prompt for dress preview.
    Image generation may still run client-side; this endpoint returns analysis + prompts.
    """
    dress_type_ref: Optional[str] = None
    gemini_generation_prompt: Optional[str] = None

    if body.reference_image_url:
        dress_type_ref = await identify_dress_type_from_reference(body.reference_image_url)
        gemini_generation_prompt = build_reference_gemini_prompt(
            dress_type=dress_type_ref,
            reference_image_url=body.reference_image_url,
            user_fabric=body.user_specified_fabric or "",
            user_color=body.user_specified_color or "",
            user_length=body.user_specified_length or "",
        )

    user_text = (body.user_prompt or body.design_description or "").strip()
    result = run_feasibility_analysis(
        fabric_pieces=[p.model_dump() for p in body.fabric_pieces],
        design_description=user_text or body.design_description,
        selected_design_ids=body.selected_design_ids,
        selected_design_names=body.selected_design_names or None,
        dress_type_from_reference=dress_type_ref,
        meas_card_text=body.meas_card_text,
    )

    # Enrich reason with reference silhouette note
    if dress_type_ref and body.reference_image_url:
        result["feasibility_analysis"]["reference_dress_type"] = dress_type_ref
        result["reference_structure_rule"] = (
            "Use reference ONLY for silhouette/cut; apply uploaded fabric for material, color, and texture."
        )

    result["gemini_generation_prompt"] = gemini_generation_prompt
    result["known_dress_types"] = list_baseline_labels()[:20]
    return result
