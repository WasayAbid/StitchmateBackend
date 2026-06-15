"""Move bead/moti/kundan titles wrongly stored under Embroidery → Beads."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))


def main() -> None:
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    rows = (
        sb.table("accessories")
        .select("id,title,category")
        .eq("is_active", True)
        .ilike("category", "Embroidery")
        .execute()
        .data
        or []
    )
    moved = 0
    for r in rows:
        t = (r.get("title") or "").lower()
        if any(k in t for k in ("bead", "moti", "kundan", "mani", "pearls")):
            sb.table("accessories").update({"category": "Beads", "subcategory": "Beads"}).eq(
                "id", r["id"]
            ).execute()
            moved += 1
    print(f"Re-tagged {moved} items from Embroidery to Beads")


if __name__ == "__main__":
    main()
