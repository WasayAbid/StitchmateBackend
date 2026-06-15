"""
Medium-size baseline fabric requirements (meters) for built-in dress styles.

These values are for a medium-sized person and are the foundation for dynamic
size-based adjustments (see fabric_feasibility.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FabricBaseline:
    dress_type: str
    min_meters: float
    max_meters: float

    @property
    def medium_baseline_meters(self) -> float:
        """Midpoint of the medium-size range."""
        return round((self.min_meters + self.max_meters) / 2, 2)


def _b(label: str, lo: float, hi: float) -> FabricBaseline:
    return FabricBaseline(dress_type=label, min_meters=lo, max_meters=hi)


# medium_size_baseline_fabric_meters — ranges from product spec
FABRIC_BASELINES: dict[str, FabricBaseline] = {}
_entries = [
    ("a_line_shirt", "A line shirt", 2.0, 2.5),
    ("anarkali_frock", "Anarkali frock", 4.0, 6.0),
    ("angrakha_frock", "Angrakha frock", 3.5, 5.0),
    ("angrakha_kurti", "Angrakha kurti", 2.5, 3.0),
    ("collar_kurti", "Collar kurti", 2.0, 2.5),
    ("collar_kurti_patiala_shalwar", "Collar kurti with patiala shalwari", 5.0, 6.5),
    ("farshi_shalwar_churidar", "Farshi shalwar and churidar pajama", 6.5, 8.5),
    ("frock_churidar", "Frock with churidar pajama", 5.5, 7.5),
    ("frock", "Frock", 3.0, 4.0),
    ("frock_sharara", "Frock with sharara", 5.5, 7.5),
    ("front_open_gown", "Front open gown", 4.0, 6.0),
    ("gharara", "Gharara", 3.5, 5.0),
    ("gown", "Gown", 4.0, 6.0),
    ("heavy_dupatta_look", "Heavy dupatta look (outfit fabric only)", 5.0, 7.0),
    ("kaftan", "Kaftan", 3.0, 4.5),
    ("lengha", "Lengha", 4.0, 6.0),
    ("long_a_line_shirt", "Long a line shirt", 2.5, 3.5),
    ("long_frock_plazzo", "Long frock plazzo", 6.0, 8.5),
    ("long_gown", "Long gown", 4.0, 6.0),
    ("long_shirt", "Long shirt", 2.5, 3.0),
    ("peplum_top", "Peplum top", 1.5, 2.0),
    ("plain_kurti", "Plain kurti", 2.0, 2.5),
    ("plain_maxi_with_kurti", "Plain maxi with kurti", 5.5, 7.5),
    ("plazo_set", "Plazo set", 4.5, 6.0),
    ("plazo", "Plazo", 2.5, 3.5),
    ("saree", "Saree", 5.5, 6.5),
    ("shalwar_kameez", "Shalwar kameez (Kameez & Shalwar only)", 4.0, 5.0),
    ("sharara", "Sharara", 2.5, 3.5),
    ("shirt_plazzo", "Shirt plazzo", 4.5, 6.0),
    ("shirt_trouser", "Shirt trouser", 3.5, 4.5),
    ("shirt_churidar", "Shirt with churidar pajama", 4.5, 6.0),
    ("short_shirt_shalwar", "Short shirt and shalwar", 3.5, 4.5),
    ("short_shirt_plazzo", "Short shirt plazzo", 4.0, 5.5),
    ("side_cut_kurti", "Side cut kurti", 2.5, 3.0),
    ("silk_shirt_plazzo", "Silk shirt plazzo", 4.5, 6.0),
    ("suit_3_piece", "Suit (3-piece)", 6.0, 8.0),
    # Legacy / alias labels
    ("shirt_plazo", "Shirt & Plazo", 4.5, 6.0),
    ("bridal_frock", "Bridal Frock", 3.0, 4.0),
    ("simple_kurta", "Simple Kurta", 2.0, 2.5),
    ("gents_kurta", "Gents Kurta", 2.5, 3.0),
]
for key, label, lo, hi in _entries:
    FABRIC_BASELINES[key] = _b(label, lo, hi)

# Map legacy frontend IDs and common aliases → baseline keys
_ID_ALIASES: dict[str, str] = {
    "fusion": "shirt_plazzo",
    "bridal": "bridal_frock",
    "casual": "plain_kurti",
    "anarkali": "anarkali_frock",
    "lehenga": "lengha",
    "salwar": "shalwar_kameez",
    "gents-kurta": "gents_kurta",
    "shirt-plazo": "shirt_plazzo",
    "shirt-plazzo": "shirt_plazzo",
}


def normalize_key(text: str) -> str:
    return (
        text.lower()
        .replace("&", "and")
        .replace("'", "")
        .replace("-", "_")
        .replace(" ", "_")
        .strip("_")
    )


def lookup_baseline(name_or_id: str) -> Optional[FabricBaseline]:
    if not name_or_id or not name_or_id.strip():
        return None
    raw = name_or_id.strip()
    key = normalize_key(raw)
    if key in _ID_ALIASES:
        key = _ID_ALIASES[key]
    if key in FABRIC_BASELINES:
        return FABRIC_BASELINES[key]

    # dresses-a-line-shirt → a_line_shirt
    if key.startswith("dresses_"):
        key = key[len("dresses_") :]
    if key in FABRIC_BASELINES:
        return FABRIC_BASELINES[key]

    for alias, target in _ID_ALIASES.items():
        if alias in key or key in alias:
            return FABRIC_BASELINES.get(target)

    # Fuzzy: label substring match
    for baseline_key, baseline in FABRIC_BASELINES.items():
        label_norm = normalize_key(baseline.dress_type)
        if key in label_norm or label_norm in key:
            return baseline
        if baseline_key.replace("_", "") in key.replace("_", ""):
            return baseline
    return None


def list_baseline_labels() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for b in FABRIC_BASELINES.values():
        if b.dress_type not in seen:
            seen.add(b.dress_type)
            out.append(b.dress_type)
    return sorted(out)
