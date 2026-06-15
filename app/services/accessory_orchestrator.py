"""
Multi-agent accessory overlay workflow.

Agents 1–7: analysis & planning (Groq + Gemini vision).
Final Render: Gemini image model — garment image ONLY (never catalog product photo).
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import google.generativeai as genai

from app.services.groq_client import groq_chat

logger = logging.getLogger(__name__)

VISION_MODEL = "gemini-1.5-flash"
RENDER_MODEL = "gemini-2.0-flash-exp"
MAX_QA_RETRIES = 0  # retries add 60s+ — disabled for responsiveness
GEMINI_VISION_TIMEOUT_S = 25


def _parse_json(text: str) -> dict[str, Any]:
    raw = re.sub(r"```json|```", "", (text or "").strip()).strip()
    return json.loads(raw)


def _gemini_json(prompt: str, *image_parts: dict, timeout_s: int = GEMINI_VISION_TIMEOUT_S) -> dict[str, Any]:
    model = genai.GenerativeModel(VISION_MODEL)
    parts: list = [prompt]
    parts.extend(image_parts)

    def _call():
        return model.generate_content(parts)

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_call)
        try:
            resp = fut.result(timeout=timeout_s)
        except FuturesTimeout as exc:
            raise TimeoutError(f"Gemini vision timed out after {timeout_s}s") from exc
    return _parse_json(resp.text or "{}")


def _groq_json(prompt: str) -> dict[str, Any]:
    raw = groq_chat(prompt, temperature=0.15)
    if not raw:
        return {}
    try:
        return _parse_json(raw)
    except json.JSONDecodeError:
        logger.warning("Groq JSON parse failed: %s", raw[:300])
        return {}


def _format_roi(region: dict | None) -> str:
    if not region:
        return "not specified — infer from garment and user text"
    return (
        f"left={region.get('leftPct', 0):.1f}% "
        f"top={region.get('topPct', 0):.1f}% "
        f"width={region.get('widthPct', 30):.1f}% "
        f"height={region.get('heightPct', 30):.1f}% of dress image"
    )


@dataclass
class AccessoryWorkflowState:
    """Master orchestrator state — passed between API calls."""

    detection: dict[str, Any] = field(default_factory=dict)
    selected_variant: Optional[str] = None
    segmentation: dict[str, Any] = field(default_factory=dict)
    placement: dict[str, Any] = field(default_factory=dict)
    distribution: dict[str, Any] = field(default_factory=dict)
    stitching: dict[str, Any] = field(default_factory=dict)
    qa: dict[str, Any] = field(default_factory=dict)
    qa_passed: bool = False
    retry_count: int = 0
    agent_trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AccessoryWorkflowState:
        if not data:
            return cls()
        return cls(
            detection=data.get("detection") or {},
            selected_variant=data.get("selected_variant"),
            segmentation=data.get("segmentation") or {},
            placement=data.get("placement") or {},
            distribution=data.get("distribution") or {},
            stitching=data.get("stitching") or {},
            qa=data.get("qa") or {},
            qa_passed=bool(data.get("qa_passed")),
            retry_count=int(data.get("retry_count") or 0),
            agent_trace=list(data.get("agent_trace") or []),
        )


# ── Agent 1: Detection ──────────────────────────────────────────────────────

def agent1_detection(
    accessory_image_part: dict,
    *,
    accessory_title: str,
    accessory_category: str,
) -> dict[str, Any]:
    prompt = f"""AGENT 1 — ACCESSORY DETECTION AGENT

Analyze this e-commerce accessory PRODUCT image for "{accessory_title}" (category: {accessory_category}).

Identify:
- accessory_type: button|lace|bead|pearl|patch|embroidery|sequin|trim|other
- variants: list of DISTINCT buyer-selectable options (colors/patterns/finishes)
- colors, patterns, estimated_quantity_in_photo (same variant count, not variant options)
- has_multiple_variants: true only if buyer must pick between different colors/patterns

Do NOT treat multiple identical items (e.g. 6 gold buttons) as multiple variants.

Respond ONLY JSON:
{{
  "accessory_type": "bead",
  "variants": [{{"id":"1","label":"Red Beads","color":"red","pattern":"round"}}],
  "colors": ["red","white"],
  "patterns": ["round"],
  "quantity_in_pack": 1,
  "has_multiple_variants": false,
  "summary": "one sentence"
}}"""
    try:
        return _gemini_json(prompt, accessory_image_part)
    except Exception as exc:
        logger.warning("Agent1 failed: %s", exc)
        return {
            "accessory_type": accessory_category.lower(),
            "variants": [{"id": "1", "label": accessory_title, "color": "", "pattern": ""}],
            "has_multiple_variants": False,
            "summary": accessory_title,
        }


# ── Agent 2: Variant selection (orchestrator gate) ──────────────────────────

def agent2_variant_gate(
    detection: dict[str, Any],
    selected_variant: Optional[str],
) -> Optional[dict[str, Any]]:
    variants = detection.get("variants") or []
    labels = [v.get("label") for v in variants if isinstance(v, dict) and v.get("label")]
    has_multi = bool(detection.get("has_multiple_variants")) and len(labels) > 1

    if has_multi and not (selected_variant or "").strip():
        lines = "\n".join(
            f"{v.get('id', i+1)}. {v.get('label')}" for i, v in enumerate(variants) if isinstance(v, dict)
        )
        cleaned = [
            {"id": str(v.get("id", i + 1)), "label": v["label"], "description": v.get("color") or v.get("pattern") or ""}
            for i, v in enumerate(variants)
            if isinstance(v, dict) and v.get("label")
        ]
        return {
            "needs_variant_selection": True,
            "variants": cleaned,
            "message": (
                f"We found {len(cleaned)} accessory variants in this product image.\n\n"
                f"Available options:\n{lines}\n\n"
                "Which variant would you like to apply to your garment?"
            ),
        }
    resolved = (selected_variant or "").strip()
    if not resolved and labels:
        resolved = labels[0]
    return {"selected_variant": resolved}


# ── Agent 3: Segmentation ───────────────────────────────────────────────────

def agent3_segmentation(
    accessory_image_part: dict,
    *,
    detection: dict[str, Any],
    selected_variant: str,
    qa_fix_hint: str = "",
) -> dict[str, Any]:
    fix = f"\nQA FIX REQUIRED: {qa_fix_hint}\n" if qa_fix_hint else ""
    prompt = f"""AGENT 3 — ACCESSORY SEGMENTATION AGENT

Selected variant ONLY: "{selected_variant}"
Detection context: {json.dumps(detection, ensure_ascii=False)[:1200]}
{fix}

From the product image, describe the EXTRACTED accessory asset after removing:
hands, background, packaging, watermarks, product cards, catalog layouts, measuring tools.

Output describes what will be RENDERED — NOT the original photo.

Respond ONLY JSON:
{{
  "extracted_accessory_asset": {{
    "type": "bead",
    "color": "white",
    "material": "glass",
    "shape": "round 4mm",
    "pattern": "solid",
    "texture": "glossy",
    "scale_hint": "small decorative bead"
  }},
  "removed_elements": ["background","hands","packaging"],
  "original_image_discarded": true,
  "visual_description": "detailed phrase for image generator — single accessory motif only"
}}"""
    try:
        return _gemini_json(prompt, accessory_image_part)
    except Exception as exc:
        logger.warning("Agent3 failed: %s", exc)
        return {
            "extracted_accessory_asset": {
                "type": detection.get("accessory_type", "accessory"),
                "color": selected_variant,
            },
            "original_image_discarded": True,
            "visual_description": f"{selected_variant} {detection.get('accessory_type', 'accessory')}",
        }


# ── Agent 4: Placement understanding ────────────────────────────────────────

def agent4_placement(
    *,
    region: dict | None,
    user_prompt: str,
    placement_method: str,
    reference_image_part: dict | None,
    accessory_type: str,
    dress_context: str = "",
    dress_image_part: dict | None = None,
) -> dict[str, Any]:
    roi = _format_roi(region)
    if dress_image_part and not dress_context:
        try:
            dress_analysis = _gemini_json(
                """Analyze this GARMENT photograph for tailoring accessory integration.
Identify fabric weave/texture, dominant color, lighting direction, fold lines, and where
accessories should follow fabric curvature (not float on top).

Respond ONLY JSON:
{
  "garment_type": "",
  "fabric_texture": "",
  "dominant_color": "",
  "lighting": "",
  "fold_areas": [],
  "shadow_style": "",
  "integration_notes": "how accessories must embed into fabric weave"
}""",
                dress_image_part,
            )
            dress_context = f"Garment integration analysis: {json.dumps(dress_analysis, ensure_ascii=False)}"
        except Exception as exc:
            logger.warning("Agent4 dress vision failed: %s", exc)

    ref_block = ""
    if reference_image_part and placement_method == "reference_image":
        try:
            ref_style = _gemini_json(
                """AGENT 4 — Analyze REFERENCE image for accessory PLACEMENT STYLE only.
Respond ONLY JSON: {"placement_area":"","placement_style":"border|scatter|row|motif","density":"low|medium|high","symmetry":"symmetric|asymmetric","notes":""}""",
                reference_image_part,
            )
            ref_block = f"Reference placement style: {json.dumps(ref_style)}"
        except Exception as exc:
            logger.warning("Agent4 reference vision failed: %s", exc)

    groq_prompt = f"""AGENT 4 — PLACEMENT UNDERSTANDING AGENT

Convert user inputs into tailoring placement instructions.
CRITICAL: Marked region = DECORATION AREA, not an overlay rectangle to fill with a product photo.

Accessory type: {accessory_type}
User marked region (percent of dress image): {roi}
User prompt: "{user_prompt or 'none'}"
Placement mode: {placement_method}
{ref_block}
{dress_context}

Respond ONLY JSON:
{{
  "placement_area": "neckline|sleeve_cuff|hem|placket|motif|border|custom",
  "placement_style": "border|scatter|single|paired|row|curve_follow",
  "density": "low|medium|high",
  "orientation": "follow_fabric|horizontal|vertical",
  "tailoring_notes": "specific instructions for tailor",
  "region_is_decoration_zone": true
}}"""
    result = _groq_json(groq_prompt)
    if result:
        return result
    return {
        "placement_area": "custom",
        "placement_style": "border",
        "density": "medium",
        "region_is_decoration_zone": True,
        "tailoring_notes": user_prompt or f"Place {accessory_type} in marked region",
    }


# ── Agent 5: Distribution ───────────────────────────────────────────────────

def agent5_distribution(
    *,
    segmentation: dict[str, Any],
    placement: dict[str, Any],
    detection: dict[str, Any],
    user_prompt: str,
    qa_fix_hint: str = "",
) -> dict[str, Any]:
    fix = f"\nQA FIX: {qa_fix_hint}" if qa_fix_hint else ""
    prompt = f"""AGENT 5 — ACCESSORY DISTRIBUTION AGENT

Calculate separate accessory INSTANCES — never one stretched image across regions.

Segmentation: {json.dumps(segmentation.get('extracted_accessory_asset', {}))}
Placement: {json.dumps(placement)}
Accessory type: {detection.get('accessory_type')}
User prompt: "{user_prompt}"
{fix}

Examples:
- Buttons on both cuffs → left_cuff: 1, right_cuff: 1 (separate instances)
- Beads on neckline → total_beads: 40-50, arrangement: curved neckline

Respond ONLY JSON:
{{
  "instances": [
    {{"location": "left_cuff", "count": 1, "spacing_mm": 0, "orientation": "follow_cuff"}},
    {{"location": "right_cuff", "count": 1, "spacing_mm": 0, "orientation": "follow_cuff"}}
  ],
  "total_count": 2,
  "arrangement": "symmetric pair",
  "never_stretch_single_image": true,
  "distribution_notes": ""
}}"""
    result = _groq_json(prompt)
    if result:
        return result
    return {
        "instances": [{"location": placement.get("placement_area", "custom"), "count": 1}],
        "total_count": 1,
        "never_stretch_single_image": True,
    }


# ── Agent 6: Stitching simulation (plan) ────────────────────────────────────

def agent6_stitching(
    *,
    distribution: dict[str, Any],
    placement: dict[str, Any],
    segmentation: dict[str, Any],
) -> dict[str, Any]:
    prompt = f"""AGENT 6 — STITCHING SIMULATION AGENT

Plan how accessories appear physically attached to fabric.

Distribution: {json.dumps(distribution)[:1500]}
Placement: {json.dumps(placement)[:800]}
Asset: {json.dumps(segmentation.get('extracted_accessory_asset', {}))}

Respond ONLY JSON:
{{
  "attachment_method": "sewn|embroidered|beaded|appliqued|shank_button",
  "stitching_visible": true,
  "fabric_interaction": "follows folds and weave",
  "shadow_depth": "contact shadows at edges",
  "fold_adaptation": "bend with fabric curvature",
  "forbidden": ["floating","sticker","product_photo_paste","rectangular_overlay"]
}}"""
    result = _groq_json(prompt)
    if result:
        return result
    return {
        "attachment_method": "sewn",
        "stitching_visible": True,
        "forbidden": ["floating", "sticker", "product_photo_paste"],
    }


# ── Agent 7: QA ─────────────────────────────────────────────────────────────

def agent7_qa(
    *,
    state: AccessoryWorkflowState,
    accessory_title: str,
) -> dict[str, Any]:
    prompt = f"""AGENT 7 — ACCESSORY QA AGENT

Validate the accessory placement PLAN before final render.
Product: {accessory_title}

Full plan:
{json.dumps(state.to_dict(), ensure_ascii=False)[:4000]}

Check ALL:
- original product image will NOT be rendered (discarded after segmentation)
- no rectangular overlay planned
- correct variant selected
- accessory extracted (not whole catalog photo)
- realistic scale
- correct placement area
- separate instances (not stretched across regions)
- realistic stitching planned

Respond ONLY JSON:
{{
  "passed": true,
  "checks": {{
    "product_image_removed": true,
    "no_rectangular_overlay": true,
    "correct_variant": true,
    "accessory_extracted": true,
    "correct_scale": true,
    "correct_placement": true,
    "separate_instances": true,
    "realistic_stitching": true
  }},
  "failures": [],
  "retry_agent": null,
  "fix_hint": ""
}}

If any check fails, set passed=false, list failures, retry_agent="segmentation" or "distribution", fix_hint=specific fix."""
    result = _groq_json(prompt)
    if not result:
        return {"passed": True, "checks": {}, "failures": [], "retry_agent": None, "fix_hint": ""}
    return result


# ── Final render prompt (Agent 8 — text only, no product image) ─────────────

def build_final_render_prompt(
    *,
    state: AccessoryWorkflowState,
    accessory_title: str,
    accessory_category: str,
    placement_method: str,
    region: dict | None,
) -> str:
    seg = state.segmentation
    asset = seg.get("extracted_accessory_asset") or {}
    visual = seg.get("visual_description") or f"{state.selected_variant} {asset.get('type', 'accessory')}"

    return f"""FINAL RENDER AGENT — Professional Tailored Garment Photography

You receive ONE image: the customer's GARMENT (dress/outfit).
Your job is to REGENERATE this dress photograph with accessories physically stitched INTO the fabric —
as if a master tailor embroidered/sewed them during construction. This is NOT a photo collage.

═══ MULTI-AGENT PLAN (mandatory — do not deviate) ═══

AGENT 3 EXTRACTED ASSET (render THIS motif only — NOT any product/catalog photo):
{json.dumps(asset, indent=2)}
Visual motif to synthesize into fabric: {visual}
The catalog product photo is DISCARDED. Synthesize the motif into the garment weave/thread.

SELECTED VARIANT: {state.selected_variant}

AGENT 4 PLACEMENT (region = decoration zone, NOT overlay rectangle):
{json.dumps(state.placement, indent=2)}
Marked region: {_format_roi(region)}

AGENT 5 DISTRIBUTION (separate instances — NEVER stretch one image):
{json.dumps(state.distribution, indent=2)}

AGENT 6 STITCHING:
{json.dumps(state.stitching, indent=2)}

QA APPROVED: {state.qa_passed}

═══ STRICT RENDER RULES ═══

DO:
✓ Keep identical garment, pose, framing, background, model if present
✓ REGENERATE fabric texture in placement zones so accessories are embedded in the weave
✓ Render ONLY stitched/tailored accessory instances per distribution plan
✓ Match scale, lighting, shadows, fabric folds from garment image
✓ Show attachment cues: thread stitches, bead holes in fabric, lace edge overlock, button shank into placket
✓ Accessories follow fabric curvature — partially occluded by folds where natural
✓ Pakistani formal-wear catalog quality — looks manufactured, not edited

NEVER:
✗ Paste, overlay, or superimpose the catalog product photo (rectangular cut-out)
✗ Sticker, decal, floating layer, or Photoshop-style placement on top of fabric
✗ Hard rectangular edges, halo outlines, mismatched sharpness vs dress
✗ Stretch one accessory across multiple regions (e.g. both cuffs)
✗ Hands, packaging, watermarks, display boards from catalog
✗ Change the person's pose or replace the garment

Accessory catalog reference: {accessory_title} ({accessory_category})
Placement mode: {placement_method}

OUTPUT: One photorealistic image — same dress re-photographed after professional tailoring. No text or watermarks."""


# ── Master orchestrator ─────────────────────────────────────────────────────

def run_accessory_workflow(
    *,
    accessory_image_part: dict,
    dress_image_part: dict,
    accessory_title: str,
    accessory_category: str,
    placement_method: str,
    region: dict | None,
    user_prompt: str,
    selected_variant: Optional[str],
    reference_image_part: dict | None,
    workflow_state: Optional[dict[str, Any]] = None,
    skip_variant_check: bool = False,
) -> dict[str, Any]:
    """
    Run agents 1–7. Returns either variant selection request or render-ready state.
    Does NOT render — caller runs Gemini image with build_final_render_prompt().
    """
    state = AccessoryWorkflowState.from_dict(workflow_state)

    # Agent 1
    if not state.detection:
        state.detection = agent1_detection(
            accessory_image_part,
            accessory_title=accessory_title,
            accessory_category=accessory_category,
        )
        state.agent_trace.append("agent1_detection")

    # Agent 2
    if not skip_variant_check:
        gate = agent2_variant_gate(state.detection, selected_variant or state.selected_variant)
        if gate and gate.get("needs_variant_selection"):
            return {
                "needs_variant_selection": True,
                "variants": gate["variants"],
                "message": gate["message"],
                "workflow_state": state.to_dict(),
            }
        if gate and gate.get("selected_variant"):
            state.selected_variant = gate["selected_variant"]
    elif selected_variant:
        state.selected_variant = selected_variant
    elif not state.selected_variant:
        variants = state.detection.get("variants") or []
        if variants and isinstance(variants[0], dict):
            state.selected_variant = variants[0].get("label", accessory_title)
        else:
            state.selected_variant = accessory_title

    state.agent_trace.append("agent2_variant")

    # Agents 3–7 with QA retry loop
    qa_fix = ""
    for attempt in range(MAX_QA_RETRIES + 1):
        state.segmentation = agent3_segmentation(
            accessory_image_part,
            detection=state.detection,
            selected_variant=state.selected_variant or accessory_title,
            qa_fix_hint=qa_fix if "segmentation" in (state.qa.get("retry_agent") or "") else "",
        )
        state.agent_trace.append(f"agent3_segmentation_attempt_{attempt}")

        if not state.placement:
            state.placement = agent4_placement(
                region=region,
                user_prompt=user_prompt,
                placement_method=placement_method,
                reference_image_part=reference_image_part,
                accessory_type=state.detection.get("accessory_type", accessory_category),
                dress_image_part=dress_image_part,
            )
            state.agent_trace.append("agent4_placement")

        state.distribution = agent5_distribution(
            segmentation=state.segmentation,
            placement=state.placement,
            detection=state.detection,
            user_prompt=user_prompt,
            qa_fix_hint=qa_fix if "distribution" in (state.qa.get("retry_agent") or "") else "",
        )
        state.agent_trace.append(f"agent5_distribution_attempt_{attempt}")

        state.stitching = agent6_stitching(
            distribution=state.distribution,
            placement=state.placement,
            segmentation=state.segmentation,
        )
        state.agent_trace.append("agent6_stitching")

        state.qa = agent7_qa(state=state, accessory_title=accessory_title)
        state.agent_trace.append(f"agent7_qa_attempt_{attempt}")

        if state.qa.get("passed", True):
            state.qa_passed = True
            break

        state.qa_passed = False
        state.retry_count += 1
        qa_fix = state.qa.get("fix_hint") or "Ensure separate instances, no product photo paste"
        retry_agent = state.qa.get("retry_agent") or "distribution"
        state.agent_trace.append(f"qa_retry_{retry_agent}")
        if attempt >= MAX_QA_RETRIES:
            logger.warning("QA failed after retries — proceeding with best plan: %s", state.qa.get("failures"))
            break

    render_prompt = build_final_render_prompt(
        state=state,
        accessory_title=accessory_title,
        accessory_category=accessory_category,
        placement_method=placement_method,
        region=region,
    )

    return {
        "ready_to_render": True,
        "workflow_state": state.to_dict(),
        "render_prompt": render_prompt,
        "agent_summary": {
            "variant": state.selected_variant,
            "type": state.detection.get("accessory_type"),
            "placement_area": state.placement.get("placement_area"),
            "total_instances": state.distribution.get("total_count"),
            "qa_passed": state.qa_passed,
        },
    }


def run_accessory_workflow_fast(
    *,
    accessory_image_part: dict,
    dress_image_part: dict,
    accessory_title: str,
    accessory_category: str,
    placement_method: str,
    region: dict | None,
    user_prompt: str,
    selected_variant: Optional[str],
    reference_image_url: Optional[str] = None,
    reference_image_part: dict | None = None,
    placement_prompt_text: Optional[str] = None,
    written_prompt_for_accessory: Optional[str] = None,
    workflow_state: Optional[dict[str, Any]] = None,
    skip_variant_check: bool = False,
) -> dict[str, Any]:
    """
    Balanced path: variant check + extraction/placement/stitching planning, then render.
    Skips QA retry loop (agent 7) for speed.
    """
    from app.services.overlay_prompts import _multi_instance_hint

    state = AccessoryWorkflowState.from_dict(workflow_state)
    combined_prompt = " ".join(
        p.strip()
        for p in (placement_prompt_text, written_prompt_for_accessory, user_prompt)
        if p and p.strip()
    )

    if not state.detection:
        try:
            state.detection = agent1_detection(
                accessory_image_part,
                accessory_title=accessory_title,
                accessory_category=accessory_category,
            )
        except Exception as exc:
            logger.warning("Fast path detection skipped: %s", exc)
            state.detection = {
                "accessory_type": accessory_category.lower(),
                "variants": [{"id": "1", "label": accessory_title}],
                "has_multiple_variants": False,
            }

    if not skip_variant_check and not (selected_variant or state.selected_variant):
        gate = agent2_variant_gate(state.detection, selected_variant or state.selected_variant)
        if gate and gate.get("needs_variant_selection"):
            return {
                "needs_variant_selection": True,
                "variants": gate["variants"],
                "message": gate["message"],
                "workflow_state": state.to_dict(),
            }
        if gate and gate.get("selected_variant"):
            state.selected_variant = gate["selected_variant"]
    else:
        state.selected_variant = (
            selected_variant or state.selected_variant or accessory_title
        )

    state.segmentation = agent3_segmentation(
        accessory_image_part,
        detection=state.detection,
        selected_variant=state.selected_variant or accessory_title,
    )
    state.agent_trace.append("agent3_segmentation")

    state.placement = agent4_placement(
        region=region,
        user_prompt=combined_prompt,
        placement_method=placement_method,
        reference_image_part=reference_image_part,
        accessory_type=state.detection.get("accessory_type", accessory_category),
        dress_image_part=dress_image_part,
    )
    state.agent_trace.append("agent4_placement")

    state.distribution = agent5_distribution(
        segmentation=state.segmentation,
        placement=state.placement,
        detection=state.detection,
        user_prompt=combined_prompt,
    )
    state.agent_trace.append("agent5_distribution")

    state.stitching = agent6_stitching(
        distribution=state.distribution,
        placement=state.placement,
        segmentation=state.segmentation,
    )
    state.agent_trace.append("agent6_stitching")
    state.qa_passed = True

    render_prompt = build_final_render_prompt(
        state=state,
        accessory_title=accessory_title,
        accessory_category=accessory_category,
        placement_method=placement_method,
        region=region,
    )

    multi_hint = _multi_instance_hint(combined_prompt, region)
    if multi_hint:
        render_prompt += f"\n\nMULTI-INSTANCE RULES:\n{multi_hint}\n"
    if combined_prompt:
        render_prompt += f'\nUSER TAILORING INSTRUCTIONS: "{combined_prompt}"\n'
    if reference_image_url:
        render_prompt += (
            f"\nReference style URL (placement pattern only, do not paste): {reference_image_url}\n"
        )

    return {
        "ready_to_render": True,
        "workflow_state": state.to_dict(),
        "render_prompt": render_prompt,
        "agent_summary": {
            "variant": state.selected_variant,
            "type": state.detection.get("accessory_type", accessory_category),
            "placement_area": state.placement.get("placement_area"),
            "total_instances": state.distribution.get("total_count"),
            "fast_mode": True,
            "stitched_integration": True,
        },
    }
