"""GET /accessories, GET /accessories/{id} — Supabase-backed catalog."""
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from supabase import Client, create_client

logger = logging.getLogger(__name__)

acc_router = APIRouter(prefix="/accessories", tags=["accessories"])


def _supabase_key() -> str:
    return (
        os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_KEY")
        or os.environ["SUPABASE_SERVICE_KEY"]
    )


def _anon() -> Client:
    url = os.environ.get("SUPABASE_URL")
    if not url:
        raise RuntimeError("SUPABASE_URL is not set")
    return create_client(url, _supabase_key())


@acc_router.get("")
async def list_accessories(
    search: Optional[str] = Query(None),
    category: Optional[list[str]] = Query(
        None,
        description="One or more categories, e.g. ?category=Lace&category=Beads",
    ),
    subcategory: Optional[str] = Query(None),
    price_min: Optional[float] = Query(None, ge=0),
    price_max: Optional[float] = Query(None, ge=0),
    is_active: bool = Query(True),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=200),
):
    sb = _anon()
    q = (
        sb.table("accessories")
        .select("id,title,price,currency,image_url,product_url,category,subcategory")
        .eq("is_active", is_active)
    )
    if category:
        names = [c.strip() for c in category if c and c.strip()]
        if len(names) == 1:
            q = q.ilike("category", names[0])
        elif len(names) > 1:
            q = q.in_("category", names)
    if subcategory:
        q = q.ilike("subcategory", subcategory)
    if price_min is not None:
        q = q.gte("price", price_min)
    if price_max is not None:
        q = q.lte("price", price_max)
    if search:
        term = search.strip()
        if term:
            q = q.or_(f"title.ilike.%{term}%,description.ilike.%{term}%")
    offset = (page - 1) * page_size
    try:
        resp = q.range(offset, offset + page_size - 1).order("created_at", desc=True).execute()
    except Exception as exc:
        logger.exception("list_accessories failed")
        raise HTTPException(500, f"Failed to load accessories: {exc}") from exc
    rows = resp.data or []
    return {
        "page": page,
        "page_size": page_size,
        "count": len(rows),
        "total": getattr(resp, "count", None),
        "data": rows,
    }


@acc_router.get("/{accessory_id}")
async def get_accessory(accessory_id: str):
    sb = _anon()
    resp = sb.table("accessories").select("*").eq("id", accessory_id).single().execute()
    if not resp.data:
        raise HTTPException(404, "Accessory not found")
    return resp.data
