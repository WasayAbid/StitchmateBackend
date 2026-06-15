"""Purge junk or all accessories from Supabase. Usage:
  python scripts/purge_accessories.py junk
  python scripts/purge_accessories.py all
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from app.services.accessory_catalog import is_junk_accessory  # noqa: E402


def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "junk").lower()
    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise SystemExit("SUPABASE_SERVICE_KEY required")
    sb = create_client(url, key)
    deleted = 0
    if mode == "all":
        while True:
            rows = sb.table("accessories").select("id").limit(500).execute().data or []
            if not rows:
                break
            ids = [r["id"] for r in rows]
            sb.table("accessories").delete().in_("id", ids).execute()
            deleted += len(ids)
            print(f"deleted {deleted}...")
    else:
        offset = 0
        while True:
            rows = (
                sb.table("accessories")
                .select("id,title,description,category")
                .range(offset, offset + 499)
                .execute()
                .data
                or []
            )
            if not rows:
                break
            junk = [
                r["id"]
                for r in rows
                if is_junk_accessory(
                    r.get("title") or "",
                    r.get("description") or "",
                    r.get("category") or "",
                )
            ]
            if junk:
                sb.table("accessories").delete().in_("id", junk).execute()
                deleted += len(junk)
            if len(rows) < 500:
                break
            offset += 500
    print(f"Done. mode={mode} deleted={deleted}")


if __name__ == "__main__":
    main()
