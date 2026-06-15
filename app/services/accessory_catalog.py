"""
Tailoring-only accessory catalog rules.

Daraz generic searches (e.g. "buttons") return electrical switchboards — we use
explicit tailoring search queries and reject non-fashion titles before upsert.
"""
from __future__ import annotations

import re
from typing import NamedTuple

# One search per UI category — ~30 products each via SYNC_MAX_PRODUCTS
class TailoringSearch(NamedTuple):
    query: str
    category: str
    subcategory: str


# Daraz.pk pe jo search aap karti hain — wahi phrases yahan (English + Roman Urdu)
TAILORING_SEARCHES: list[TailoringSearch] = [
    # Lace
    TailoringSearch("dress lace border trim sewing", "Lace", "Lace"),
    # Beads — multiple queries (Daraz: "beads for dresses", moti, kundan)
    TailoringSearch("beads for dresses", "Beads", "Beads"),
    TailoringSearch("beads for dress", "Beads", "Beads"),
    TailoringSearch("beads for clothes", "Beads", "Beads"),
    TailoringSearch("moti kundan beads", "Beads", "Beads"),
    TailoringSearch("kundan moti dress", "Beads", "Beads"),
    # Buttons — Daraz style + Roman Urdu
    TailoringSearch("buttons for dresses", "Buttons", "Buttons"),
    TailoringSearch("button for dress", "Buttons", "Buttons"),
    TailoringSearch("dress ke button", "Buttons", "Buttons"),
    TailoringSearch("kameez shirt button", "Buttons", "Buttons"),
    TailoringSearch("sewing shirt kurta dress buttons", "Buttons", "Buttons"),
    # Patches — Daraz: patches for dress, phulkari, mirror work
    TailoringSearch("patches for dress", "Patches", "Patches"),
    TailoringSearch("patches for dresses", "Patches", "Patches"),
    TailoringSearch("embroidery patch dress", "Patches", "Patches"),
    TailoringSearch("phulkari patch dress", "Patches", "Patches"),
    TailoringSearch("mirror work patch", "Patches", "Patches"),
    TailoringSearch("dress patch applique", "Patches", "Patches"),
    TailoringSearch("patch for clothes", "Patches", "Patches"),
    # Sequins & embroidery
    TailoringSearch("sequins for dress", "Sequins", "Sequins"),
    TailoringSearch("sequins dress fabric craft", "Sequins", "Sequins"),
    TailoringSearch("embroidered lace fabric dress", "Embroidery", "Embroidery"),
    TailoringSearch("embroidery for dress", "Embroidery", "Embroidery"),
    # Extra lace
    TailoringSearch("lace border for dress", "Lace", "Lace"),
]

# Titles/descriptions matching these are NOT tailoring accessories
JUNK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bswitch\s*board\b",
        r"\bswitchboard\b",
        r"\belectrical\b",
        r"\bwall\s+switch\b",
        r"\bmodular\s+switch\b",
        r"\bpiano\s+switch\b",
        r"\bpush\s+button\s+switch\b",
        r"\bmcb\b",
        r"\bbreaker\b",
        r"\bcircuit\s+breaker\b",
        r"\bdistribution\s+board\b",
        r"\bgang\s+box\b",
        r"\bsocket\s+outlet\b",
        r"\bpower\s+socket\b",
        r"\bplug\s+socket\b",
        r"\bextension\s+cord\b",
        r"\bhdmi\b",
        r"\bcharger\b",
        r"\brouter\b",
        r"\bmobile\s+phone\b",
        r"\bsmart\s+watch\b",
        r"\b16\s*a\b",  # amp rating on switches
        r"\b32\s*a\b",
        r"\bampere\b",
        r"\bvolt\b",
        r"\bwiring\b",
        r"\belectric\s+fan\b",
        r"\bled\s+bulb\b",
        r"\btube\s+light\b",
        r"\bcable\s+wire\b",
        r"\bdoor\s+bell\b",
        r"\bdimmer\s+switch\b",
        r"\btouch\s+switch\b",
        r"\buniversal\s+switch\b",
        r"\brocker\s+switch\b",
    )
)

# Ambiguous "button" listings must mention fashion/sewing context
_BUTTON_FASHION_HINTS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bsew",
        r"\bdress\b",
        r"\bshirt\b",
        r"\bkurta\b",
        r"\bshalwar\b",
        r"\bfabric\b",
        r"\bbridal\b",
        r"\blace\b",
        r"\bcoat\b",
        r"\bblazer\b",
        r"\bhook\b",
        r"\bsnap\b",
        r"\bcuff\b",
        r"\bpearl\b",
        r"\bmetal\s+button\b",
        r"\bshirt\s+button\b",
        r"\bdress\s+button\b",
        r"\btailor",
        r"\bgarment\b",
        r"\btrouser\b",
        r"\bpant\b",
        r"\bsuit\b",
        r"\bpack\b",
        r"\bpcs\b",
        r"\bpiece\b",
        r"\bhole\b",
        r"\btransparent\b",
        r"\bgolden\b",
        r"\bgold\b",
        r"\bsilver\b",
        r"\bplastic\b",
        r"\bmetal\b",
        r"\bcoat\b",
        r"\blazer\b",
        r"\bjeans\b",
        r"\bkameez\b",
        r"\bshalwar\b",
        r"\bsuit\b",
        r"\bset\b",
        r"\bdozen\b",
    )
)


def is_junk_accessory(title: str, description: str = "", category: str = "") -> bool:
    text = f"{title} {description} {category}"
    for pat in JUNK_PATTERNS:
        if pat.search(text):
            return True
    # Electrical "button" without fashion context
    if re.search(r"\bbutton", text, re.I) and category.lower() == "buttons":
        if not any(h.search(text) for h in _BUTTON_FASHION_HINTS):
            if re.search(r"\bswitch\b|\belectric|\bboard\b|\bmodule\b", text, re.I):
                return True
    return False


def category_for_search_query(query: str) -> tuple[str, str]:
    """Map a Daraz search phrase to DB category (for extra SYNC_KEYWORDS entries)."""
    lower = (query or "").lower()
    # Beads / buttons pehle — warna "beads ... embroidery" galat Embroidery ban jata tha
    if "bead" in lower or "moti" in lower or "kundan" in lower or "mani" in lower:
        return "Beads", "Beads"
    if "button" in lower or "btn" in lower or "batan" in lower:
        return "Buttons", "Buttons"
    if "patch" in lower or "applique" in lower or "phulkari" in lower or "motif" in lower:
        return "Patches", "Patches"
    if "sequin" in lower:
        return "Sequins", "Sequins"
    if "lace" in lower and "embroider" not in lower:
        return "Lace", "Lace"
    if "embroider" in lower:
        return "Embroidery", "Embroidery"
    return "Accessories", "General"


def filter_tailoring_products(items: list[dict]) -> tuple[list[dict], int]:
    """Drop junk rows; return (kept, rejected_count)."""
    kept: list[dict] = []
    rejected = 0
    for p in items:
        title = (p.get("title") or "").strip()
        desc = (p.get("description") or "").strip()
        cat = (p.get("category") or "").strip()
        if not title:
            rejected += 1
            continue
        if is_junk_accessory(title, desc, cat):
            rejected += 1
            continue
        kept.append(p)
    return kept, rejected
