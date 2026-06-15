"""AI Fashion Accessory Placement Engine — Gemini prompt builders."""
from __future__ import annotations

import re

_PLACEMENT_ENGINE = """
You are an AI Fashion Accessory Placement Engine.

CRITICAL: Accessory product images are REFERENCE SOURCES ONLY — never overlays, stickers, or paste-on layers.

## STEP 0: MULTI-VARIANT (if user selected a variant)
If a user-selected variant is specified, extract and use ONLY that variant. Ignore all other colors, sizes, or patterns in the product photo.

## STEP 1: ACCESSORY EXTRACTION
From the Input Accessory Image, identify and isolate ONLY the actual accessory item.
- Button image → extract button only
- Lace image → extract lace pattern only
- Beads → individual beads or bead strand pattern
- Embroidery → motif only
- Patch → patch design only

IGNORE and NEVER place on the garment: hands, backgrounds, tables, packaging, shadows, watermarks, labels, measuring scales, display boards, product cards.

Never paste, stretch, duplicate, or overlay the entire product photograph onto the dress.

## STEP 2: ACCESSORY UNDERSTANDING
Determine type, shape, color, material, pattern, and realistic scale. Build a clean accessory representation.

## STEP 3: PLACEMENT ANALYSIS
Priority: (1) user-marked region, (2) user text instructions, (3) reference image placement style, (4) tailoring rules.

When multiple placement regions are implied (e.g. "both cuffs", "left and right sleeves", symmetric borders):
- Create SEPARATE accessory instances — one per location.
- Position each instance individually following local garment geometry, angle, and orientation.
- NEVER stretch one accessory image across multiple regions.
- NEVER enlarge one instance to fill the entire selected area.

Typical tailoring placements:
- Buttons: front placket, sleeve cuffs, neck closure
- Lace: hemline, sleeves, neckline, front panel borders
- Beads: neck area, sleeve borders, motif regions
- Pearls: decorative embroidery zones

## STEP 4: REALISTIC STITCHING SIMULATION
Accessories must appear physically attached: stitching, sewing, fabric interaction, contact shadows, folds, natural depth. Sewn into fabric — never floating, pasted, or sticker-like.

## STEP 5: SCALE VALIDATION
Match garment scale: shirt-button size for buttons, small decorative size for beads, appropriate border width for lace. Reject unrealistic scaling.

## STEP 6: REFERENCE IMAGE MATCHING (when applicable)
Analyze placement pattern, density, layout, symmetry from reference. Apply the same styling approach — do NOT copy the reference garment image.

## STEP 7: VISUAL REALISM CHECK (before output)
Verify: accessory extracted correctly; background/hands/packaging removed; correct placement, scale, orientation, symmetry; separate instances where needed; realistic stitching; no floating/pasted/stretched product image.

FINAL: The garment must look professionally tailored with accessories physically stitched in — not an edited collage.
"""


def _user_return_prompt_text(
    *,
    user_return_prompt_text: str | None = None,
    placement_prompt_text: str | None = None,
    written_prompt_for_accessory: str | None = None,
) -> str | None:
    if user_return_prompt_text and user_return_prompt_text.strip():
        return user_return_prompt_text.strip()
    parts = [p.strip() for p in (placement_prompt_text, written_prompt_for_accessory) if p and p.strip()]
    return " ".join(parts) if parts else None


def _format_roi(region: dict) -> str:
    return (
        f"left={region.get('leftPct', 0):.1f}% "
        f"top={region.get('topPct', 0):.1f}% "
        f"width={region.get('widthPct', 30):.1f}% "
        f"height={region.get('heightPct', 30):.1f}%"
    )


def _multi_instance_hint(user_guidance: str | None, region: dict | None) -> str:
    hints: list[str] = []
    if user_guidance:
        lower = user_guidance.lower()
        if re.search(r"\b(both|left and right|each cuff|each sleeve|symmetric|pair of)\b", lower):
            hints.append(
                "User text implies MULTIPLE placement locations — create separate accessory instances "
                "for each side/region; do not stretch one image across both."
            )
        if re.search(r"\b(cuffs?|sleeves?)\b", lower) and "both" in lower:
            hints.append(
                "Place one properly scaled instance on the LEFT cuff and one on the RIGHT cuff."
            )
        if re.search(r"\b(buttons?)\b", lower) and re.search(r"\b(\d+|multiple|several)\b", lower):
            hints.append(
                "Place individual buttons at correct spacing — not one enlarged button covering the area."
            )

    if region:
        w = float(region.get("widthPct", 0))
        h = float(region.get("heightPct", 0))
        if w > 45 and h > 25:
            hints.append(
                "The marked region may span multiple garment areas — infer logical sub-locations "
                "(e.g. left/right symmetry) and place separate instances; never fill the whole box "
                "with one stretched product photo."
            )
    return " ".join(hints)


def build_overlay_prompt(
    *,
    dress_image_url: str,
    accessory_image_url: str,
    accessory_title: str,
    accessory_category: str,
    placement_method: str,
    region: dict | None = None,
    mask_data: str | None = None,
    reference_image_url: str | None = None,
    clarification_prompt_text: str | None = None,
    written_prompt_for_reference: str | None = None,
    user_return_prompt_text: str | None = None,
    placement_prompt_text: str | None = None,
    written_prompt_for_accessory: str | None = None,
    selected_variant: str | None = None,
) -> str:
    """
    Construct the full Gemini instruction for accessory placement (not overlay paste).

    placement_method: auto | roi | reference_image
    """
    user_guidance = _user_return_prompt_text(
        user_return_prompt_text=user_return_prompt_text,
        placement_prompt_text=placement_prompt_text,
        written_prompt_for_accessory=written_prompt_for_accessory,
    )

    ref_clarification = (clarification_prompt_text or "").strip()
    if not ref_clarification and written_prompt_for_reference:
        ref_clarification = written_prompt_for_reference.strip()

    multi_hint = _multi_instance_hint(user_guidance, region)

    if placement_method == "roi":
        roi_desc = mask_data.strip() if mask_data and mask_data.strip() else (
            _format_roi(region) if region else "user-designated region (see dress image)"
        )
        placement_block = (
            "PLACEMENT SOURCE: User-marked region (Priority 1).\n"
            f"Target zone on dress: {roi_desc}.\n"
            "Within or aligned to this zone, place extracted accessory INSTANCE(S) at appropriate "
            "garment landmarks — NOT by pasting the full product image into the rectangle.\n"
            "Each instance must respect local fabric curvature, orientation, and scale.\n"
            "Do NOT stretch a single accessory photo to cover the entire marked area."
        )
    elif placement_method == "reference_image":
        ref_url = reference_image_url or "(reference image attached)"
        placement_block = (
            "PLACEMENT SOURCE: Reference image placement style (Priority 3).\n"
            f"Reference Image: {ref_url}\n"
            "Analyze placement pattern, density, layout, and symmetry from the reference. "
            "Replicate the STYLING APPROACH on the user's dress using the extracted catalog accessory — "
            "do NOT copy the reference garment or paste reference photos."
        )
        if ref_clarification:
            placement_block += f'\nReference clarification: "{ref_clarification}"'
    else:
        placement_block = (
            "PLACEMENT SOURCE: Intelligent tailoring placement (Priority 4 + category rules).\n"
            f"Accessory: '{accessory_title}' (category: {accessory_category}).\n"
            "Choose natural, professional placement for this accessory type on the dress."
        )

    variant_block = ""
    if selected_variant and selected_variant.strip():
        variant_block = (
            f'\nUSER-SELECTED VARIANT (mandatory): "{selected_variant.strip()}"\n'
            "Extract and use ONLY this variant from the product image. "
            "Discard all other colors, patterns, and sizes shown in the photo.\n"
        )

    user_block = ""
    if user_guidance:
        user_block = (
            f'\nUSER TEXT INSTRUCTIONS (Priority 2):\n"{user_guidance}"\n'
            "Follow these instructions for placement locations and styling.\n"
        )

    multi_block = ""
    if multi_hint:
        multi_block = f"\nMULTI-INSTANCE RULES:\n{multi_hint}\n"

    return f"""{_PLACEMENT_ENGINE}
---
Input Dress Image: {dress_image_url}
Input Accessory Image (reference only — extract object, do not paste whole image): {accessory_image_url}
Accessory catalog: {accessory_title} | {accessory_category}
{variant_block}
{placement_block}
{user_block}
{multi_block}
---
TASK: REGENERATE the dress photograph with the extracted accessory stitched INTO the fabric weave —
as if tailored during manufacture. The accessory must look sewn/embroidered/beaded into the garment,
not placed on top like a sticker or Photoshop layer.

HARD CONSTRAINTS:
1. Output dress MUST match Input Dress Image (same garment, pose, framing, background).
2. Synthesize ONLY the extracted accessory motif (color, material, pattern) into fabric — discard the catalog photo frame.
3. Photorealistic tailoring photography — thread interaction, fabric folds over accessory edges, contact shadows.
4. Never sticker, decal, cut-out, floating layer, rectangular paste, or collage.

FORBIDDEN:
- Pasting the entire accessory product image onto the garment
- Stretching one accessory across multiple regions (e.g. both cuffs)
- Enlarging one instance to fill a selection rectangle
- Hands, packaging, backgrounds, watermarks visible on the dress
- Hard rectangular edges, halo outlines, mismatched blur vs dress fabric
- Replacing the dress or changing pose/background

OUTPUT: One photorealistic image — same dress with accessories physically integrated. No text or watermarks."""
