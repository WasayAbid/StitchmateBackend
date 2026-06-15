"""Execute accessory overlay — fast path by default, hard timeouts."""
from __future__ import annotations

import base64
import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Optional

import httpx

from app.services.accessory_orchestrator import (
    run_accessory_workflow,
    run_accessory_workflow_fast,
)
from app.services.gemini_image_client import generate_image_from_parts

logger = logging.getLogger(__name__)

RENDER_TIMEOUT_S = int(os.environ.get("OVERLAY_RENDER_TIMEOUT_S", "90"))
WORKFLOW_TIMEOUT_S = int(os.environ.get("OVERLAY_WORKFLOW_TIMEOUT_S", "90"))


def _img_part(url: str) -> dict:
    if url.startswith("blob:"):
        raise RuntimeError(
            "Dress image uses a temporary browser URL. Reload the page or re-select your design, then try again."
        )
    if url.startswith("data:"):
        header, b64data = url.split(",", 1)
        mime = "image/jpeg"
        if ":" in header:
            mime = header.split(":")[1].split(";")[0] or mime
        return {"inline_data": {"mime_type": mime, "data": b64data}}
    data = httpx.get(url, timeout=20, follow_redirects=True).content
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(data).decode()}}


def _normalize_placement_mode(
    placement_mode: str,
    placement_method: str | None,
    has_region: bool,
    has_reference: bool,
) -> str:
    method = (placement_method or placement_mode or "auto").strip().lower()
    aliases = {
        "region": "roi",
        "roi": "roi",
        "reference": "reference_image",
        "reference_image": "reference_image",
        "auto": "auto",
    }
    resolved = aliases.get(method, "auto")
    if resolved == "reference_image" and has_reference:
        return "reference_image"
    if resolved == "roi" and has_region:
        return "roi"
    if resolved in ("reference_image", "roi") and not (has_reference or has_region):
        return "auto"
    return resolved


def _user_prompt(body: Any) -> str:
    parts = [
        getattr(body, "user_return_prompt_text", None),
        getattr(body, "placement_prompt_text", None),
        getattr(body, "written_prompt_for_accessory", None),
    ]
    text = " ".join(p.strip() for p in parts if p and p.strip())
    if getattr(body, "clarification_prompt", None):
        text = f"{text} {body.clarification_prompt}".strip()
    if getattr(body, "written_prompt_for_reference", None):
        text = f"{text} Reference style: {body.written_prompt_for_reference}".strip()
    return text


def _run_with_timeout(fn, timeout_s: int, label: str):
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        try:
            return fut.result(timeout=timeout_s)
        except FuturesTimeout as exc:
            raise TimeoutError(f"{label} timed out after {timeout_s}s") from exc


def execute_overlay(*, body: Any, accessory: dict) -> dict[str, Any]:
    dress_part = _img_part(body.dress_image_url)
    acc_part = _img_part(accessory["image_url"])

    has_region = bool(body.region_of_interest)
    has_reference = bool(body.reference_image_url)
    placement_method = _normalize_placement_mode(
        body.placement_mode,
        body.placement_method,
        has_region,
        has_reference,
    )

    ref_part = (
        _img_part(body.reference_image_url)
        if placement_method == "reference_image" and body.reference_image_url
        else None
    )

    user_prompt = _user_prompt(body)
    use_multi = bool(getattr(body, "use_multi_agent", False))

    def _workflow():
        if use_multi:
            return run_accessory_workflow(
                accessory_image_part=acc_part,
                dress_image_part=dress_part,
                accessory_title=accessory["title"],
                accessory_category=accessory.get("category") or "Accessories",
                placement_method=placement_method,
                region=body.region_of_interest,
                user_prompt=user_prompt,
                selected_variant=body.selected_variant,
                reference_image_part=ref_part,
                workflow_state=body.workflow_state,
                skip_variant_check=body.skip_variant_check,
            )
        return run_accessory_workflow_fast(
            accessory_image_part=acc_part,
            dress_image_part=dress_part,
            accessory_title=accessory["title"],
            accessory_category=accessory.get("category") or "Accessories",
            placement_method=placement_method,
            region=body.region_of_interest,
            user_prompt=user_prompt,
            selected_variant=body.selected_variant,
            reference_image_url=body.reference_image_url,
            reference_image_part=ref_part,
            placement_prompt_text=body.placement_prompt_text,
            written_prompt_for_accessory=body.written_prompt_for_accessory,
            workflow_state=body.workflow_state,
            skip_variant_check=body.skip_variant_check,
        )

    workflow_result = _run_with_timeout(_workflow, WORKFLOW_TIMEOUT_S, "Accessory planning")

    if workflow_result.get("needs_variant_selection"):
        return {
            "needs_variant_selection": True,
            "variants": workflow_result.get("variants", []),
            "message": workflow_result.get("message"),
            "workflow_state": workflow_result.get("workflow_state"),
        }

    render_prompt = workflow_result.get("render_prompt", "")
    if not render_prompt:
        raise RuntimeError("Workflow did not produce a render prompt")

    render_parts: list = [
        render_prompt,
        (
            "═══ GARMENT PHOTO TO REGENERATE ═══\n"
            "Edit THIS dress image only. Stitch the planned accessory motif INTO the fabric — "
            "never paste a catalog product photo on top. Output one photorealistic tailored garment."
        ),
        dress_part,
    ]
    if ref_part and placement_method == "reference_image":
        render_parts.append(
            "REFERENCE IMAGE (placement style only — do NOT copy this garment or paste this photo):"
        )
        render_parts.append(ref_part)

    def _render() -> str:
        return generate_image_from_parts(render_parts, timeout_s=RENDER_TIMEOUT_S)

    image_data_url = _run_with_timeout(_render, RENDER_TIMEOUT_S + 5, "Image generation")

    agent_summary = workflow_result.get("agent_summary") or {}
    return {
        "imageDataUrl": image_data_url,
        "workflow_state": workflow_result.get("workflow_state"),
        "agent_summary": agent_summary,
    }
