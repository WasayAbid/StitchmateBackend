"""Persist user_history — Supabase Postgres table (preferred) or Storage fallback."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from supabase import Client, create_client

logger = logging.getLogger(__name__)

ALLOWED_ACTIVITY_TYPES = frozenset(
    {"design_studio", "virtual_try_on", "accessory_overlay"}
)

HISTORY_BUCKET = "user-history"
STORAGE_PREFIX = "entries"

_table_available: bool | None = None


def _service() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_KEY")
    )
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY required for user_history")
    return create_client(url, key)


def _sanitize_json(data: dict[str, Any], *, max_str: int = 8000) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, str) and v.startswith("data:image") and len(v) > max_str:
            out[k] = v[:200] + f"...[truncated {len(v)} chars]"
            out[f"{k}_truncated"] = True
        elif isinstance(v, dict):
            out[k] = _sanitize_json(v, max_str=max_str)
        else:
            out[k] = v
    return out


def _normalize_related_id(related_resource_id: Optional[str]) -> Optional[str]:
    if not related_resource_id:
        return None
    try:
        UUID(related_resource_id)
        return related_resource_id
    except ValueError:
        return None


def table_exists() -> bool:
    global _table_available
    if _table_available is not None:
        return _table_available
    try:
        sb = _service()
        sb.table("user_history").select("id").limit(1).execute()
        _table_available = True
    except Exception as exc:
        logger.warning("user_history table not available: %s", exc)
        _table_available = False
    return _table_available


def reset_table_cache() -> None:
    global _table_available
    _table_available = None


def _storage_path(user_id: str, entry_id: str) -> str:
    return f"{STORAGE_PREFIX}/{user_id}/{entry_id}.json"


def _insert_storage(
    *,
    user_id: str,
    activity_type: str,
    input_details: dict[str, Any],
    output_details: dict[str, Any],
    related_resource_id: Optional[str],
) -> dict[str, Any]:
    sb = _service()
    entry_id = str(uuid.uuid4())
    row = {
        "id": entry_id,
        "user_id": user_id,
        "activity_type": activity_type,
        "input_details": input_details,
        "output_details": output_details,
        "related_resource_id": related_resource_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "storage_fallback": True,
    }
    path = _storage_path(user_id, entry_id)
    payload = json.dumps(row, ensure_ascii=False).encode("utf-8")
    sb.storage.from_(HISTORY_BUCKET).upload(
        path,
        payload,
        file_options={"content-type": "application/json", "upsert": "true"},
    )
    return row


def _list_storage(
    *,
    user_id: str,
    activity_type: Optional[str],
    limit: int,
) -> list[dict[str, Any]]:
    sb = _service()
    prefix = f"{STORAGE_PREFIX}/{user_id}/"
    try:
        objects = sb.storage.from_(HISTORY_BUCKET).list(prefix.rstrip("/"))
    except Exception as exc:
        logger.warning("list storage history failed: %s", exc)
        return []

    rows: list[dict[str, Any]] = []
    for obj in objects or []:
        name = obj.get("name") if isinstance(obj, dict) else getattr(obj, "name", None)
        if not name or not str(name).endswith(".json"):
            continue
        path = f"{prefix}{name}"
        try:
            raw = sb.storage.from_(HISTORY_BUCKET).download(path)
            row = json.loads(raw.decode("utf-8"))
            if activity_type and row.get("activity_type") != activity_type:
                continue
            rows.append(row)
        except Exception as exc:
            logger.warning("read storage history %s: %s", path, exc)

    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows[:limit]


def insert_user_history(
    *,
    user_id: str,
    activity_type: str,
    input_details: dict[str, Any],
    output_details: dict[str, Any],
    related_resource_id: Optional[str] = None,
) -> dict[str, Any]:
    if activity_type not in ALLOWED_ACTIVITY_TYPES:
        raise ValueError(f"Invalid activity_type: {activity_type}")

    clean_in = _sanitize_json(input_details)
    clean_out = _sanitize_json(output_details)
    rel = _normalize_related_id(related_resource_id)

    if table_exists():
        row = {
            "user_id": user_id,
            "activity_type": activity_type,
            "input_details": clean_in,
            "output_details": clean_out,
            "related_resource_id": rel,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        sb = _service()
        resp = sb.table("user_history").insert(row).execute()
        data = resp.data or []
        return data[0] if data else row

    logger.info("user_history table missing — saving to storage bucket %s", HISTORY_BUCKET)
    return _insert_storage(
        user_id=user_id,
        activity_type=activity_type,
        input_details=clean_in,
        output_details=clean_out,
        related_resource_id=rel,
    )


def list_user_history(
    *,
    user_id: str,
    activity_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if activity_type and activity_type not in ALLOWED_ACTIVITY_TYPES:
        raise ValueError(f"Invalid activity_type: {activity_type}")

    cap = min(limit, 200)
    db_rows: list[dict[str, Any]] = []

    if table_exists():
        sb = _service()
        q = (
            sb.table("user_history")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(cap)
        )
        if activity_type:
            q = q.eq("activity_type", activity_type)
        resp = q.execute()
        db_rows = resp.data or []

    storage_rows = _list_storage(user_id=user_id, activity_type=activity_type, limit=cap)

    if not db_rows and not storage_rows:
        return []

    by_id: dict[str, dict[str, Any]] = {}
    for row in db_rows + storage_rows:
        rid = row.get("id")
        if rid:
            by_id[str(rid)] = row

    merged = sorted(
        by_id.values(),
        key=lambda r: r.get("created_at") or "",
        reverse=True,
    )
    return merged[:cap]


def _get_storage_entry(user_id: str, entry_id: str) -> dict[str, Any] | None:
    sb = _service()
    path = _storage_path(user_id, entry_id)
    try:
        raw = sb.storage.from_(HISTORY_BUCKET).download(path)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _update_storage(
    *,
    user_id: str,
    entry_id: str,
    input_details: dict[str, Any] | None,
    output_details: dict[str, Any] | None,
) -> dict[str, Any] | None:
    row = _get_storage_entry(user_id, entry_id)
    if not row or row.get("user_id") != user_id:
        return None
    if input_details is not None:
        row["input_details"] = _sanitize_json(input_details)
    if output_details is not None:
        row["output_details"] = _sanitize_json(output_details)
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    sb = _service()
    path = _storage_path(user_id, entry_id)
    payload = json.dumps(row, ensure_ascii=False).encode("utf-8")
    sb.storage.from_(HISTORY_BUCKET).upload(
        path,
        payload,
        file_options={"content-type": "application/json", "upsert": "true"},
    )
    return row


def _delete_storage(user_id: str, entry_id: str) -> bool:
    sb = _service()
    path = _storage_path(user_id, entry_id)
    try:
        sb.storage.from_(HISTORY_BUCKET).remove([path])
        return True
    except Exception as exc:
        logger.warning("delete storage history %s: %s", path, exc)
        return False


def update_user_history(
    *,
    user_id: str,
    entry_id: str,
    input_details: dict[str, Any] | None = None,
    output_details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if input_details is None and output_details is None:
        raise ValueError("Nothing to update")

    if table_exists():
        sb = _service()
        existing = (
            sb.table("user_history")
            .select("*")
            .eq("id", entry_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            patch: dict[str, Any] = {}
            if input_details is not None:
                patch["input_details"] = _sanitize_json(input_details)
            if output_details is not None:
                patch["output_details"] = _sanitize_json(output_details)
            resp = (
                sb.table("user_history")
                .update(patch)
                .eq("id", entry_id)
                .eq("user_id", user_id)
                .execute()
            )
            data = resp.data or []
            if data:
                return data[0]

    updated = _update_storage(
        user_id=user_id,
        entry_id=entry_id,
        input_details=input_details,
        output_details=output_details,
    )
    if updated:
        return updated

    raise LookupError("History entry not found")


def delete_user_history(*, user_id: str, entry_id: str) -> bool:
    deleted = False
    if table_exists():
        sb = _service()
        resp = (
            sb.table("user_history")
            .delete()
            .eq("id", entry_id)
            .eq("user_id", user_id)
            .execute()
        )
        if resp.data:
            deleted = True

    if _delete_storage(user_id, entry_id):
        deleted = True

    return deleted
