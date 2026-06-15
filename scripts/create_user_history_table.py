#!/usr/bin/env python3
"""
Create public.user_history in Supabase Postgres.

Set SUPABASE_DB_URL in stichmate-backend/.env (Supabase Dashboard → Settings → Database → URI),
then run:

  python scripts/create_user_history_table.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT.parent / "bolt-stichmate" / "supabase" / "migrations" / "20260523000000_user_history.sql"
)


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    db_url = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL", "")
    if not db_url.startswith("postgresql"):
        print(
            "Set SUPABASE_DB_URL in stichmate-backend/.env to your Supabase Postgres connection string,\n"
            "then run this script again.\n\n"
            "Or paste this SQL in Supabase Dashboard → SQL Editor:\n"
        )
        print(MIGRATION.read_text(encoding="utf-8"))
        return 1

    try:
        import psycopg2
    except ImportError:
        print("Install psycopg2-binary: pip install psycopg2-binary")
        return 1

    sql = MIGRATION.read_text(encoding="utf-8")
    print("Applying user_history migration…")
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()

    print("Done — user_history table is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
