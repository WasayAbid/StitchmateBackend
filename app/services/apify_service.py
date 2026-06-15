"""
Apify Daraz.pk scraper — shahidirfan~Daraz-pk-Scraper

Actor input (required shape):
  searchQuery, maxProducts, maxPages — NOT search/maxItems/country

All searches are tailoring-accessory queries (lace, beads, dress buttons, etc.)
— never generic "buttons" which returns electrical switchboards.
"""
import logging
import os
import time
from typing import Any, Callable, Optional

import httpx

from app.services.accessory_catalog import (
    TAILORING_SEARCHES,
    TailoringSearch,
    category_for_search_query,
    filter_tailoring_products,
    is_junk_accessory,
)

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"


def _parse_env_keywords() -> list[TailoringSearch] | None:
    """
    Optional SYNC_KEYWORDS — comma-separated tailoring Daraz search queries.
    - Pipe form: "button for dress|Buttons|Buttons"
    - Plain form: category inferred from words (button→Buttons, bead→Beads, etc.)
    """
    raw = (os.environ.get("SYNC_KEYWORDS") or "").strip()
    if not raw:
        return None
    defaults = list(TAILORING_SEARCHES)
    specs: list[TailoringSearch] = []
    for i, part in enumerate(p.strip() for p in raw.split(",") if p.strip()):
        if "|" in part:
            bits = [b.strip() for b in part.split("|")]
            if len(bits) >= 3:
                specs.append(TailoringSearch(bits[0], bits[1], bits[2]))
                continue
        cat, sub = category_for_search_query(part)
        if cat == "Accessories" and i < len(defaults):
            cat, sub = defaults[i].category, defaults[i].subcategory
        specs.append(TailoringSearch(part, cat, sub))
    return specs or None


def tailoring_searches() -> list[TailoringSearch]:
    return _parse_env_keywords() or list(TAILORING_SEARCHES)


def searches_for_sync(only_categories: Optional[list[str]] = None) -> list[TailoringSearch]:
    """
    When only_categories is set (e.g. Lace,Patches), always use built-in TAILORING_SEARCHES
    for those categories — SYNC_KEYWORDS may only list Buttons/Beads and would yield 0 runs.
    """
    if only_categories:
        allowed = {c.strip().lower() for c in only_categories if c.strip()}
        specs = [s for s in TAILORING_SEARCHES if s.category.lower() in allowed]
        if specs:
            return specs
        logger.warning("No built-in searches for categories: %s", only_categories)
    return tailoring_searches()


def _max_products() -> int:
    try:
        return max(1, min(100, int(os.environ.get("SYNC_MAX_PRODUCTS", "30"))))
    except ValueError:
        return 30


def _max_pages() -> int:
    try:
        return max(1, min(20, int(os.environ.get("SYNC_MAX_PAGES", "3"))))
    except ValueError:
        return 3


def _config() -> tuple[str, str, dict[str, str]]:
    token = (os.environ.get("APIFY_API_TOKEN") or "").strip()
    actor_raw = (os.environ.get("APIFY_ACTOR_ID") or "shahidirfan/Daraz-pk-Scraper").strip()
    if not token:
        raise RuntimeError(
            "APIFY_API_TOKEN is missing. Set it in stichmate-backend/.env or bolt-stichmate/.env"
        )
    actor_id = actor_raw.replace("/", "~")
    headers = {"Authorization": f"Bearer {token}"}
    return token, actor_id, headers


def _actor_input(keyword: str) -> dict[str, Any]:
    return {
        "searchQuery": keyword,
        "maxProducts": _max_products(),
        "maxPages": _max_pages(),
        "includeOutOfStock": False,
        "proxyConfiguration": {"useApifyProxy": True},
    }


def _trigger(keyword: str, headers: dict[str, str], actor_id: str) -> str:
    url = f"{APIFY_BASE}/actors/{actor_id}/runs"
    payload = _actor_input(keyword)
    r = httpx.post(
        url,
        json=payload,
        headers=headers,
        timeout=60,
        params={"memory": 1024},
    )
    if r.status_code >= 400:
        logger.error("Apify trigger failed %s: %s", r.status_code, r.text[:500])
        r.raise_for_status()
    return r.json()["data"]["id"]


def _wait(run_id: str, headers: dict[str, str], poll: int = 8, max_wait: int = 360) -> None:
    url = f"{APIFY_BASE}/actor-runs/{run_id}"
    for _ in range(max_wait // poll):
        status = httpx.get(url, headers=headers, timeout=30).json()["data"]["status"]
        if status == "SUCCEEDED":
            return
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id}: {status}")
        time.sleep(poll)
    raise TimeoutError(f"Apify run {run_id} timed out after {max_wait}s")


def _fetch(run_id: str, headers: dict[str, str]) -> list[dict]:
    r = httpx.get(
        f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
        params={"format": "json", "clean": "true"},
        headers=headers,
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def _map(raw: dict, spec: TailoringSearch) -> dict | None:
    title = (raw.get("title") or raw.get("name") or "").strip()
    product_url = (raw.get("productUrl") or raw.get("url") or "").strip()
    external_id = str(
        raw.get("sku")
        or raw.get("productId")
        or raw.get("skuId")
        or product_url
        or ""
    ).strip()
    if not title or not external_id:
        return None
    description = (raw.get("description") or "")[:2000]
    if is_junk_accessory(title, description, spec.category):
        return None
    price_raw = raw.get("price") or raw.get("salePrice") or raw.get("priceText") or 0
    try:
        price = float(str(price_raw).replace(",", "").replace("PKR", "").replace("Rs.", "").strip() or 0)
    except ValueError:
        price = 0.0
    image_url = raw.get("imageUrl") or raw.get("image") or ""
    if isinstance(raw.get("thumbnails"), list) and raw["thumbnails"] and not image_url:
        image_url = raw["thumbnails"][0]
    return {
        "external_product_id": external_id,
        "title": title,
        "price": price,
        "currency": "PKR",
        "image_url": image_url,
        "product_url": product_url or "https://www.daraz.pk/",
        "category": spec.category,
        "subcategory": spec.subcategory,
        "description": description,
        "seller_name": raw.get("sellerName") or raw.get("seller") or "",
        "source_platform": "daraz",
        "tags": [spec.query, spec.category],
        "is_active": True,
    }


def scrape_keyword(spec: TailoringSearch) -> list[dict]:
    _, actor_id, headers = _config()
    run_id = _trigger(spec.query, headers, actor_id)
    logger.info("Apify run started for '%s' [%s]: %s", spec.query, spec.category, run_id)
    _wait(run_id, headers)
    raw_items = _fetch(run_id, headers)
    mapped: list[dict] = []
    skipped_junk = 0
    for row in raw_items:
        item = _map(row, spec)
        if item:
            mapped.append(item)
        else:
            title = (row.get("title") or row.get("name") or "").strip()
            if title:
                skipped_junk += 1
    logger.info(
        "Keyword '%s' [%s]: %d raw → %d kept (%d filtered as non-tailoring)",
        spec.query,
        spec.category,
        len(raw_items),
        len(mapped),
        skipped_junk,
    )
    return mapped


def scrape_all(
    on_progress: Optional[Callable[..., None]] = None,
    max_keywords: Optional[int] = None,
    only_categories: Optional[list[str]] = None,
) -> list[dict]:
    errors: list[str] = []
    all_items: list[dict] = []
    _config()

    specs = searches_for_sync(only_categories)
    if max_keywords is not None:
        specs = specs[: max(1, max_keywords)]

    total = len(specs)
    if total == 0:
        logger.error("No search queries to run (check SYNC_KEYWORDS or only_categories)")
        return []
    for i, spec in enumerate(specs, start=1):
        if on_progress:
            on_progress(
                phase="scraping",
                keyword=spec.query,
                category=spec.category,
                keyword_index=i,
                keyword_total=total,
                items_so_far=len(all_items),
                errors=errors,
            )
        try:
            all_items.extend(scrape_keyword(spec))
        except Exception as e:
            msg = f"{spec.query}: {e}"
            errors.append(msg)
            logger.error("Keyword failed — %s", msg)

    filtered, rejected = filter_tailoring_products(all_items)
    if rejected:
        logger.info("Post-scrape filter removed %d non-tailoring items", rejected)

    if not filtered and errors:
        logger.error("All Apify keywords failed. First error: %s", errors[0])

    return filtered
