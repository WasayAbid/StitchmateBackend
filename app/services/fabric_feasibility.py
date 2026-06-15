"""
Dynamic fabric feasibility: medium baselines + size multipliers + available fabric check.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.services.fabric_baselines import FabricBaseline, lookup_baseline
from app.services.groq_client import estimate_fabric_with_groq, estimate_unknown_dress_baseline_groq

logger = logging.getLogger(__name__)

# Configurable size multipliers (medium = 1.0)
SIZE_MULTIPLIERS: dict[str, float] = {
    "xs": 0.9,
    "extra_small": 0.9,
    "small": 0.95,
    "s": 0.95,
    "medium": 1.0,
    "m": 1.0,
    "regular": 1.0,
    "large": 1.25,
    "l": 1.25,
    "xl": 1.5,
    "extra_large": 1.5,
    "xxl": 1.75,
    "2xl": 1.75,
    "3xl": 2.0,
    "plus": 1.5,
    "plus_sized": 1.5,
    "plus-size": 1.5,
    "plus_size": 1.5,
}

_SIZE_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"\b(3xl|xxxl)\b", re.I), "3XL", 2.0),
    (re.compile(r"\b(2xl|xxl)\b", re.I), "XXL", 1.75),
    (re.compile(r"\b(xl|extra[\s-]?large)\b", re.I), "XL", 1.5),
    (re.compile(r"\b(large|size\s*l\b|\bl\b)", re.I), "Large", 1.25),
    (re.compile(r"\b(small|size\s*s\b|\bs\b)", re.I), "Small", 0.95),
    (re.compile(r"\b(medium|size\s*m\b|\bm\b)", re.I), "Medium", 1.0),
    (re.compile(r"\bplus[\s-]?siz", re.I), "Plus-sized", 1.5),
    (re.compile(r"\blarge\s+person\b", re.I), "Large", 1.25),
]

_MEASUREMENT_HINT = re.compile(
    r"\b(bust|waist|hip|height|chest)\s*[:=]?\s*[\d'\"]+|"
    r"\b\d+\s*(?:inches?|in\b|cm|feet|ft|'|\")",
    re.I,
)


def _length_to_meters(value: float, unit: str | None) -> float:
    u = (unit or "meters").lower().strip()
    if u.startswith("in"):
        return value * 0.0254
    if u.startswith("ft"):
        return value * 0.3048
    if u.startswith("cm"):
        return value / 100.0
    return value


def available_fabric_meters(fabric_pieces: list[dict[str, Any]]) -> Optional[float]:
    lengths: list[float] = []
    for p in fabric_pieces:
        length = p.get("length")
        if length is None:
            continue
        try:
            lengths.append(_length_to_meters(float(length), p.get("unit")))
        except (TypeError, ValueError):
            continue
    if not lengths:
        return None
    return round(sum(lengths), 2)


def detect_size_from_text(text: str) -> tuple[str, float, bool]:
    """Return (size_label, multiplier, has_measurement_hints)."""
    if not text or not text.strip():
        return "medium", 1.0, False
    has_meas = bool(_MEASUREMENT_HINT.search(text))
    for pattern, label, mult in _SIZE_PATTERNS:
        if pattern.search(text):
            return label, mult, has_meas
    return "medium", 1.0, has_meas


def resolve_baselines_for_selection(
    selected_design_ids: list[str],
    selected_design_names: list[str] | None = None,
    design_description: str = "",
) -> list[FabricBaseline]:
    found: list[FabricBaseline] = []
    seen: set[str] = set()

    for design_id in selected_design_ids:
        b = lookup_baseline(design_id)
        if b and b.dress_type not in seen:
            found.append(b)
            seen.add(b.dress_type)

    if selected_design_names:
        for name in selected_design_names:
            b = lookup_baseline(name)
            if b and b.dress_type not in seen:
                found.append(b)
                seen.add(b.dress_type)

    if not found and design_description.strip():
        # Try to match dress type phrases in free text
        for token in re.split(r"[,;\n]+", design_description):
            b = lookup_baseline(token.strip())
            if b and b.dress_type not in seen:
                found.append(b)
                seen.add(b.dress_type)

    return found


def build_reference_gemini_prompt(
    *,
    dress_type: str,
    reference_image_url: str,
    user_fabric: str = "",
    user_color: str = "",
    user_length: str = "",
) -> str:
    params = []
    if user_fabric:
        params.append(f"user_specified_fabric: {user_fabric}")
    if user_color:
        params.append(f"user_specified_color: {user_color}")
    if user_length:
        params.append(f"user_specified_length: {user_length}")
    param_block = ", ".join(params) if params else "use uploaded fabric swatches for all material appearance"

    return (
        f"Generate a dress based on the dress_type of {dress_type} (silhouette from reference: {reference_image_url}). "
        f"The dress should have ONLY the silhouette and cut of the reference image. "
        "Do NOT use the fabric, color, embroidery, lace, beads, prints, or decorative surface details from the reference image. "
        f"Instead, apply the following parameters: {param_block}. "
        "Output: professional catalog garment visualization — dress only, no human model."
    )


def run_feasibility_analysis(
    *,
    fabric_pieces: list[dict[str, Any]],
    design_description: str = "",
    selected_design_ids: list[str] | None = None,
    selected_design_names: list[str] | None = None,
    dress_type_from_reference: str | None = None,
    meas_card_text: str | None = None,
) -> dict[str, Any]:
    selected_design_ids = selected_design_ids or []
    combined_text = " ".join(
        filter(
            None,
            [design_description, meas_card_text or ""],
        )
    ).strip()

    baselines = resolve_baselines_for_selection(
        selected_design_ids,
        selected_design_names,
        design_description,
    )

    dress_type_label: str
    baseline_medium: float

    if baselines:
        dress_type_label = " + ".join(b.dress_type for b in baselines)
        baseline_medium = round(sum(b.medium_baseline_meters for b in baselines), 2)
    elif dress_type_from_reference:
        dress_type_label = dress_type_from_reference
        b = lookup_baseline(dress_type_from_reference)
        if b:
            baseline_medium = b.medium_baseline_meters
        else:
            guessed = estimate_unknown_dress_baseline_groq(dress_type_from_reference)
            baseline_medium = guessed if guessed else 4.0
    else:
        dress_type_label = "Custom garment"
        guessed = estimate_unknown_dress_baseline_groq(
            design_description[:200] or "Pakistani formal dress"
        )
        baseline_medium = guessed if guessed else 4.0

    size_label, multiplier, has_measurements = detect_size_from_text(combined_text)
    size_details = combined_text[:500] if (size_label != "medium" or has_measurements) else ""

    groq_used = False
    if size_details and (size_label != "medium" or has_measurements):
        groq_result = estimate_fabric_with_groq(
            dress_type=dress_type_label,
            baseline_meters=baseline_medium,
            size_details=size_details or size_label,
        )
        if groq_result:
            groq_used = True
            try:
                minimum = float(groq_result["minimum_fabric_meters"])
                size_label = str(groq_result.get("size_label") or size_label)
                multiplier = float(groq_result.get("multiplier") or multiplier)
            except (TypeError, ValueError):
                minimum = round(baseline_medium * multiplier, 2)
        else:
            minimum = round(baseline_medium * multiplier, 2)
    else:
        minimum = round(baseline_medium * multiplier, 2)

    available = available_fabric_meters(fabric_pieces)
    if available is not None:
        feasible = available >= minimum * 0.98
        if feasible:
            reason = (
                f"Your fabric (~{available} m) meets the estimated need for this design at size {size_label}."
            )
        else:
            shortfall = round(minimum - available, 1)
            reason = (
                f"Insufficient fabric: you have ~{available} m but need at least ~{minimum} m "
                f"for size {size_label} ({shortfall} m short)."
            )
    else:
        feasible = True
        reason = (
            "Fabric length not fully specified — upload measurements to confirm. "
            f"Estimated requirement shown below for your size."
        )

    summary = (
        f"Estimated minimum fabric required for a {size_label} {dress_type_label}: {minimum} meters."
    )

    return {
        "feasible": feasible,
        "reason": f"{summary} {reason}".strip(),
        "feasibility_analysis": {
            "dress_type": dress_type_label,
            "size_label": size_label,
            "baseline_meters_medium": baseline_medium,
            "size_multiplier": multiplier,
            "minimum_fabric_required": minimum,
            "available_fabric_meters": available,
            "summary": summary,
            "groq_adjustment_used": groq_used,
            "selected_baselines": [
                {
                    "dress_type": b.dress_type,
                    "medium_baseline_meters": b.medium_baseline_meters,
                    "range_meters": [b.min_meters, b.max_meters],
                }
                for b in baselines
            ],
        },
    }
