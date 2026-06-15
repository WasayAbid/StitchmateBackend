"""POST /admin/sync-accessories — Apify Daraz scrape (background job)."""
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Header, Query
from supabase import Client, create_client

from app.services.accessory_catalog import filter_tailoring_products, is_junk_accessory
from app.services.apify_service import scrape_all

logger = logging.getLogger(__name__)

sync_router = APIRouter(prefix="/admin", tags=["admin"])
SYNC_SECRET = os.environ.get("SYNC_SECRET", "change-me")

_lock = threading.Lock()
_sync_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "phase": "idle",
    "keyword": None,
    "keyword_index": 0,
    "keyword_total": 0,
    "items_so_far": 0,
    "scraped": 0,
    "upserted": 0,
    "deactivated": 0,
    "errors": [],
    "warning": None,
}


def _service() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_KEY")
    )
    if not url or not key:
        raise RuntimeError(
            "Set SUPABASE_URL and SUPABASE_SERVICE_KEY (service role) for accessory sync"
        )
    return create_client(url, key)


def _check_secret(x_sync_secret: str = Header(...)):
    if x_sync_secret != SYNC_SECRET:
        raise HTTPException(401, "Invalid sync secret")


def _update_state(**kwargs: Any) -> None:
    with _lock:
        _sync_state.update(kwargs)


def _purge_junk_rows(sb: Client) -> int:
    """Hard-delete electrical / switchboard etc. from DB."""
    deleted = 0
    offset = 0
    page = 500
    while True:
        resp = (
            sb.table("accessories")
            .select("id,title,description,category")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        junk_ids = [
            r["id"]
            for r in rows
            if is_junk_accessory(
                r.get("title") or "",
                r.get("description") or "",
                r.get("category") or "",
            )
        ]
        if junk_ids:
            sb.table("accessories").delete().in_("id", junk_ids).execute()
            deleted += len(junk_ids)
        if len(rows) < page:
            break
        offset += page
    return deleted


def _purge_all_rows(sb: Client) -> int:
    deleted = 0
    while True:
        resp = sb.table("accessories").select("id").limit(500).execute()
        rows = resp.data or []
        if not rows:
            break
        ids = [r["id"] for r in rows]
        sb.table("accessories").delete().in_("id", ids).execute()
        deleted += len(ids)
    return deleted


def _upsert_to_supabase(
    scraped: list[dict],
    only_categories: list[str] | None = None,
) -> dict[str, Any]:
    sb = _service()
    scraped, rejected = filter_tailoring_products(scraped)
    seen: dict[str, dict] = {}
    for p in scraped:
        eid = p.get("external_product_id")
        if eid and eid not in seen:
            seen[eid] = p
    unique = list(seen.values())
    if not unique:
        return {
            "scraped": 0,
            "upserted": 0,
            "deactivated": 0,
            "filtered_junk": rejected,
            "warning": (
                "Apify returned no tailoring products. Check APIFY_API_TOKEN and logs."
            ),
        }

    active_q = sb.table("accessories").select("external_product_id,category").eq("is_active", True)
    if only_categories:
        active_q = active_q.in_("category", only_categories)
    prev_ids = {r["external_product_id"] for r in (active_q.execute().data or [])}

    batch_size = 50
    upserted = 0
    for i in range(0, len(unique), batch_size):
        batch = unique[i : i + batch_size]
        r = sb.table("accessories").upsert(batch, on_conflict="external_product_id").execute()
        upserted += len(r.data or batch)

    to_deactivate = prev_ids - set(seen.keys())
    deactivated = 0
    stale = list(to_deactivate)
    for i in range(0, len(stale), batch_size):
        chunk = stale[i : i + batch_size]
        sb.table("accessories").update({"is_active": False}).in_("external_product_id", chunk).execute()
        deactivated += len(chunk)

    return {
        "scraped": len(unique),
        "upserted": upserted,
        "deactivated": deactivated,
        "filtered_junk": rejected,
    }


@sync_router.post("/purge-accessories", dependencies=[Depends(_check_secret)])
async def purge_accessories(
    mode: str = Query(
        "junk",
        description="'junk' = delete switchboard/electrical rows; 'all' = wipe entire catalog",
    ),
):
    """Remove wrong Daraz items (e.g. electrical switchboards) before re-sync."""
    if mode not in ("junk", "all"):
        raise HTTPException(400, "mode must be 'junk' or 'all'")
    sb = _service()
    if mode == "all":
        deleted = _purge_all_rows(sb)
    else:
        deleted = _purge_junk_rows(sb)
    return {
        "mode": mode,
        "deleted": deleted,
        "message": (
            "Catalog wiped. Run POST /admin/sync-accessories to fetch tailoring items."
            if mode == "all"
            else "Junk removed. Re-sync recommended to fill all 6 categories."
        ),
    }


def _run_sync_job(
    max_keywords: int | None,
    only_categories: list[str] | None = None,
) -> None:
    def on_progress(**kwargs: Any) -> None:
        _update_state(**kwargs)

    try:
        _update_state(phase="scraping", errors=[])
        scraped = scrape_all(
            on_progress=on_progress,
            max_keywords=max_keywords,
            only_categories=only_categories,
        )
        _update_state(phase="upserting", items_so_far=len(scraped))
        result = _upsert_to_supabase(scraped, only_categories=only_categories)
        _update_state(
            phase="done",
            running=False,
            finished_at=datetime.now(timezone.utc).isoformat(),
            scraped=result.get("scraped", 0),
            upserted=result.get("upserted", 0),
            deactivated=result.get("deactivated", 0),
            warning=result.get("warning"),
        )
        logger.info("Sync complete: %s", result)
    except Exception as e:
        logger.exception("Sync job failed")
        _update_state(
            phase="failed",
            running=False,
            finished_at=datetime.now(timezone.utc).isoformat(),
            errors=[str(e)],
            warning=str(e),
        )


@sync_router.get("/sync-accessories/status")
async def sync_accessories_status():
    with _lock:
        return dict(_sync_state)


@sync_router.post("/sync-accessories", dependencies=[Depends(_check_secret)])
async def sync_accessories(
    background_tasks: BackgroundTasks,
    max_keywords: int | None = Query(
        None,
        ge=1,
        le=30,
        description="Limit keywords for a faster test run (default: all configured keywords)",
    ),
    only_categories: str | None = Query(
        None,
        description="Comma-separated: only scrape these DB categories, e.g. Buttons,Beads",
    ),
    wait: bool = Query(
        False,
        description="If true, block until finished (can take 30+ min). Default: start in background.",
    ),
):
    with _lock:
        if _sync_state.get("running"):
            raise HTTPException(
                409,
                "Sync already running. Poll GET /admin/sync-accessories/status",
            )

    try:
        from app.services.apify_service import _config

        _config()
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e

    if wait:
        _update_state(
            running=True,
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
            phase="scraping",
        )
        cats = (
            [c.strip() for c in only_categories.split(",") if c.strip()]
            if only_categories
            else None
        )
        _run_sync_job(max_keywords, cats)
        with _lock:
            return dict(_sync_state)

    _update_state(
        running=True,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
        phase="starting",
        keyword=None,
        keyword_index=0,
        scraped=0,
        upserted=0,
        deactivated=0,
        errors=[],
        warning=None,
    )
    cats = (
        [c.strip() for c in only_categories.split(",") if c.strip()]
        if only_categories
        else None
    )
    background_tasks.add_task(_run_sync_job, max_keywords, cats)

    return {
        "status": "started",
        "message": (
            "Sync running in background. Each keyword takes ~2–6 minutes on Apify. "
            "Poll GET /admin/sync-accessories/status until running=false."
        ),
        "only_categories": cats,
        "poll_url": "/admin/sync-accessories/status",
        "tip": "Quick test: POST .../sync-accessories?max_keywords=1",
    }
