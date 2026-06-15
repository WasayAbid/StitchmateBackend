"""Rider portal — jobs, shifts, wallet, reviews (Supabase)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger(__name__)

FABRIC_PICKUP_REWARD = float(os.environ.get("RIDER_FABRIC_PICKUP_REWARD", "30"))
DRESS_DELIVERY_REWARD = float(os.environ.get("RIDER_DRESS_DELIVERY_REWARD", "30"))


def _sb() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reward_for(job_type: str) -> float:
    return FABRIC_PICKUP_REWARD if job_type == "fabric_pickup" else DRESS_DELIVERY_REWARD


def get_rider_profile(rider_id: str) -> dict[str, Any] | None:
    res = _sb().table("rider_profiles").select("*").eq("user_id", rider_id).limit(1).execute()
    return (res.data or [None])[0]


def upsert_rider_profile(rider_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    sb = _sb()
    patch = {**patch, "user_id": rider_id, "updated_at": _now()}
    existing = get_rider_profile(rider_id)
    if existing:
        sb.table("rider_profiles").update(patch).eq("user_id", rider_id).execute()
    else:
        sb.table("rider_profiles").insert(patch).execute()
    return get_rider_profile(rider_id) or patch


def start_shift(rider_id: str) -> dict[str, Any]:
    sb = _sb()
    now = _now()
    sb.table("rider_profiles").update(
        {"is_online": True, "shift_started_at": now, "updated_at": now}
    ).eq("user_id", rider_id).execute()
    sb.table("rider_shifts").insert({"rider_id": rider_id, "started_at": now}).execute()
    return get_rider_profile(rider_id) or {}


def end_shift(rider_id: str) -> dict[str, Any]:
    sb = _sb()
    now = _now()
    prof = get_rider_profile(rider_id) or {}
    started = prof.get("shift_started_at")
    hours = None
    if started:
        try:
            delta = datetime.fromisoformat(now.replace("Z", "+00:00")) - datetime.fromisoformat(
                str(started).replace("Z", "+00:00")
            )
            hours = round(delta.total_seconds() / 3600, 2)
        except Exception:
            hours = None
    shift = (
        sb.table("rider_shifts")
        .select("id")
        .eq("rider_id", rider_id)
        .is_("ended_at", "null")
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    if shift.data:
        sb.table("rider_shifts").update({"ended_at": now, "total_hours": hours}).eq(
            "id", shift.data[0]["id"]
        ).execute()
    sb.table("rider_profiles").update(
        {"is_online": False, "shift_started_at": None, "updated_at": now}
    ).eq("user_id", rider_id).execute()
    return get_rider_profile(rider_id) or {}


def _notify_rider(sb: Client, rider_id: str, ntype: str, title: str, message: str, job_id: str | None = None):
    try:
        sb.table("rider_notifications").insert(
            {
                "rider_id": rider_id,
                "type": ntype,
                "title": title,
                "message": message,
                "job_id": job_id,
                "read": False,
            }
        ).execute()
    except Exception as exc:
        logger.warning("rider notification failed: %s", exc)


def _profile_phone(sb: Client, user_id: str) -> str | None:
    res = sb.table("profiles").select("phone").eq("user_id", user_id).limit(1).execute()
    row = (res.data or [None])[0]
    return row.get("phone") if row else None


def create_fabric_pickup_job(confirmed_order_id: str) -> dict[str, Any] | None:
    sb = _sb()
    order = (
        sb.table("confirmed_orders").select("*").eq("id", confirmed_order_id).limit(1).execute()
    )
    co = (order.data or [None])[0]
    if not co:
        return None
    if co.get("fabric_delivery_mode") != "rider_pickup":
        return None
    existing = (
        sb.table("rider_jobs")
        .select("id")
        .eq("confirmed_order_id", confirmed_order_id)
        .eq("job_type", "fabric_pickup")
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]

    cust_phone = _profile_phone(sb, co["user_id"])
    tailor_phone = _profile_phone(sb, co["tailor_id"])
    tailor_app = (
        sb.table("tailor_applications")
        .select("shop_address")
        .eq("user_id", co["tailor_id"])
        .limit(1)
        .execute()
    )
    tailor_addr = (tailor_app.data or [{}])[0].get("shop_address") or "Tailor shop"

    row = {
        "confirmed_order_id": confirmed_order_id,
        "order_id": co.get("order_id"),
        "job_type": "fabric_pickup",
        "status": "available",
        "customer_id": co["user_id"],
        "tailor_id": co["tailor_id"],
        "customer_name": None,
        "tailor_name": co.get("tailor_shop_name") or co.get("tailor_name"),
        "customer_phone": cust_phone,
        "tailor_phone": tailor_phone,
        "pickup_address": co.get("delivery_address") or "Customer address",
        "drop_address": tailor_addr,
        "pickup_label": "Customer home",
        "drop_label": co.get("tailor_shop_name") or "Tailor shop",
        "estimated_earnings": _reward_for("fabric_pickup"),
        "updated_at": _now(),
    }
    prof = sb.table("profiles").select("full_name").eq("user_id", co["user_id"]).limit(1).execute()
    if prof.data:
        row["customer_name"] = prof.data[0].get("full_name")

    res = sb.table("rider_jobs").insert(row).select().single().execute()
    sb.table("confirmed_orders").update(
        {"fulfillment_status": "waiting_for_fabric_pickup", "updated_at": _now()}
    ).eq("id", confirmed_order_id).execute()
    return res.data


def create_dress_delivery_job(confirmed_order_id: str) -> dict[str, Any] | None:
    sb = _sb()
    order = (
        sb.table("confirmed_orders").select("*").eq("id", confirmed_order_id).limit(1).execute()
    )
    co = (order.data or [None])[0]
    if not co:
        return None
    logistics = co.get("logistics_type") or "tailor_delivery"
    if logistics == "self_drop":
        return None

    existing = (
        sb.table("rider_jobs")
        .select("id")
        .eq("confirmed_order_id", confirmed_order_id)
        .eq("job_type", "dress_delivery")
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]

    cust_phone = _profile_phone(sb, co["user_id"])
    tailor_phone = _profile_phone(sb, co["tailor_id"])
    tailor_app = (
        sb.table("tailor_applications")
        .select("shop_address")
        .eq("user_id", co["tailor_id"])
        .limit(1)
        .execute()
    )
    tailor_addr = (tailor_app.data or [{}])[0].get("shop_address") or "Tailor shop"
    customer_addr = co.get("delivery_address") or "Customer address"

    row = {
        "confirmed_order_id": confirmed_order_id,
        "order_id": co.get("order_id"),
        "job_type": "dress_delivery",
        "status": "available",
        "customer_id": co["user_id"],
        "tailor_id": co["tailor_id"],
        "customer_name": None,
        "tailor_name": co.get("tailor_shop_name") or co.get("tailor_name"),
        "customer_phone": cust_phone,
        "tailor_phone": tailor_phone,
        "pickup_address": tailor_addr,
        "drop_address": customer_addr,
        "pickup_label": co.get("tailor_shop_name") or "Tailor shop",
        "drop_label": "Customer",
        "estimated_earnings": _reward_for("dress_delivery"),
        "updated_at": _now(),
    }
    prof = sb.table("profiles").select("full_name").eq("user_id", co["user_id"]).limit(1).execute()
    if prof.data:
        row["customer_name"] = prof.data[0].get("full_name")

    res = sb.table("rider_jobs").insert(row).select().single().execute()
    sb.table("confirmed_orders").update(
        {"fulfillment_status": "rider_assigned_for_delivery", "updated_at": _now()}
    ).eq("id", confirmed_order_id).execute()
    return res.data


def tailor_confirm_fabric(confirmed_order_id: str, tailor_id: str) -> dict[str, Any]:
    sb = _sb()
    co = (
        sb.table("confirmed_orders").select("*").eq("id", confirmed_order_id).limit(1).execute()
    )
    order = (co.data or [None])[0]
    if not order:
        raise ValueError("Order not found")
    if str(order.get("tailor_id")) != str(tailor_id):
        raise ValueError("Not your order")
    now = _now()
    sb.table("confirmed_orders").update(
        {
            "fabric_received_at": now,
            "fulfillment_status": "fabric_received_by_tailor",
            "status": "in_progress",
            "updated_at": now,
        }
    ).eq("id", confirmed_order_id).execute()
    if order.get("order_id"):
        sb.table("orders").update({"status": "in_progress", "updated_at": now}).eq(
            "id", order["order_id"]
        ).execute()
    job = (
        sb.table("rider_jobs")
        .select("*")
        .eq("confirmed_order_id", confirmed_order_id)
        .eq("job_type", "fabric_pickup")
        .limit(1)
        .execute()
    )
    if job.data and job.data[0].get("status") != "completed":
        sb.table("rider_jobs").update({"status": "delivered_to_tailor", "updated_at": now}).eq(
            "id", job.data[0]["id"]
        ).execute()
    return (
        sb.table("confirmed_orders").select("*").eq("id", confirmed_order_id).limit(1).execute()
    ).data[0]


def tailor_ready_for_pickup(confirmed_order_id: str, tailor_id: str) -> dict[str, Any]:
    sb = _sb()
    co = (
        sb.table("confirmed_orders").select("*").eq("id", confirmed_order_id).limit(1).execute()
    )
    order = (co.data or [None])[0]
    if not order:
        raise ValueError("Order not found")
    if str(order.get("tailor_id")) != str(tailor_id):
        raise ValueError("Not your order")
    now = _now()
    sb.table("confirmed_orders").update(
        {
            "ready_for_pickup_at": now,
            "fulfillment_status": "ready_for_pickup",
            "delivery_status": "ready_for_pickup",
            "updated_at": now,
        }
    ).eq("id", confirmed_order_id).execute()
    create_dress_delivery_job(confirmed_order_id)
    return (
        sb.table("confirmed_orders").select("*").eq("id", confirmed_order_id).limit(1).execute()
    ).data[0]


def list_available_jobs(rider_id: str) -> list[dict[str, Any]]:
    prof = get_rider_profile(rider_id)
    if not prof or not prof.get("is_online"):
        return []
    res = (
        _sb()
        .table("rider_jobs")
        .select("*")
        .eq("status", "available")
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


def list_rider_jobs(rider_id: str, active_only: bool = False) -> list[dict[str, Any]]:
    q = _sb().table("rider_jobs").select("*").eq("rider_id", rider_id)
    if active_only:
        q = q.not_.in_("status", ["completed", "cancelled", "rejected", "available"])
    res = q.order("updated_at", desc=True).execute()
    return res.data or []


def accept_job(job_id: str, rider_id: str) -> dict[str, Any]:
    sb = _sb()
    prof = get_rider_profile(rider_id)
    if not prof or not prof.get("is_online"):
        raise ValueError("Start your shift before accepting jobs")
    job = sb.table("rider_jobs").select("*").eq("id", job_id).limit(1).execute()
    row = (job.data or [None])[0]
    if not row:
        raise ValueError("Job not found")
    if row.get("status") != "available":
        raise ValueError("Job is no longer available")
    now = _now()
    status = "accepted"
    sb.table("rider_jobs").update(
        {"rider_id": rider_id, "status": status, "accepted_at": now, "updated_at": now}
    ).eq("id", job_id).execute()
    fulfillment = (
        "rider_assigned_for_fabric_pickup"
        if row["job_type"] == "fabric_pickup"
        else "rider_assigned_for_delivery"
    )
    sb.table("confirmed_orders").update(
        {"fulfillment_status": fulfillment, "updated_at": now}
    ).eq("id", row["confirmed_order_id"]).execute()
    return sb.table("rider_jobs").select("*").eq("id", job_id).limit(1).execute().data[0]


def reject_job(job_id: str, rider_id: str) -> dict[str, Any]:
    sb = _sb()
    now = _now()
    sb.table("rider_jobs").update(
        {"rejected_at": now, "updated_at": now}
    ).eq("id", job_id).eq("status", "available").execute()
    return {"ok": True}


_FABRIC_FLOW = [
    "accepted",
    "heading_to_customer",
    "arrived_at_customer",
    "fabric_collected",
    "delivered_to_tailor",
    "completed",
]
_DRESS_FLOW = [
    "accepted",
    "heading_to_tailor",
    "collected_from_tailor",
    "out_for_delivery",
    "delivered",
    "completed",
]


def update_job_status(job_id: str, rider_id: str, new_status: str) -> dict[str, Any]:
    sb = _sb()
    job = sb.table("rider_jobs").select("*").eq("id", job_id).limit(1).execute()
    row = (job.data or [None])[0]
    if not row:
        raise ValueError("Job not found")
    if str(row.get("rider_id")) != str(rider_id):
        raise ValueError("Not your job")
    flow = _FABRIC_FLOW if row["job_type"] == "fabric_pickup" else _DRESS_FLOW
    if new_status not in flow:
        raise ValueError(f"Invalid status. Use one of: {', '.join(flow)}")
    now = _now()
    patch: dict[str, Any] = {"status": new_status, "updated_at": now}
    if new_status == "completed":
        patch["completed_at"] = now
    sb.table("rider_jobs").update(patch).eq("id", job_id).execute()

    co_patch: dict[str, Any] = {"fulfillment_status": new_status, "updated_at": now}
    if new_status == "fabric_collected":
        co_patch["fulfillment_status"] = "fabric_collected"
    elif new_status == "delivered_to_tailor":
        co_patch["fulfillment_status"] = "delivered_to_tailor"
    elif new_status == "delivered" and row["job_type"] == "dress_delivery":
        co_patch["fulfillment_status"] = "delivered"
        co_patch["delivery_status"] = "delivered"
        co_patch["status"] = "delivered"
    sb.table("confirmed_orders").update(co_patch).eq("id", row["confirmed_order_id"]).execute()

    if new_status == "completed":
        _credit_job_earnings(sb, rider_id, row)
    updated = sb.table("rider_jobs").select("*").eq("id", job_id).limit(1).execute().data[0]
    return updated


def _credit_job_earnings(sb: Client, rider_id: str, job: dict[str, Any]) -> None:
    amount = float(job.get("estimated_earnings") or _reward_for(job["job_type"]))
    dup = (
        sb.table("rider_earnings")
        .select("id")
        .eq("job_id", job["id"])
        .limit(1)
        .execute()
    )
    if dup.data:
        return
    sb.table("rider_earnings").insert(
        {
            "rider_id": rider_id,
            "job_id": job["id"],
            "confirmed_order_id": job.get("confirmed_order_id"),
            "task_type": job["job_type"],
            "amount": amount,
            "status": "credited",
        }
    ).execute()
    prof = get_rider_profile(rider_id) or {}
    wallet = float(prof.get("wallet_balance") or 0) + amount
    lifetime = float(prof.get("lifetime_earnings") or 0) + amount
    pickups = int(prof.get("completed_pickups") or 0)
    deliveries = int(prof.get("completed_deliveries") or 0)
    if job["job_type"] == "fabric_pickup":
        pickups += 1
    else:
        deliveries += 1
    sb.table("rider_profiles").update(
        {
            "wallet_balance": wallet,
            "lifetime_earnings": lifetime,
            "completed_pickups": pickups,
            "completed_deliveries": deliveries,
            "updated_at": _now(),
        }
    ).eq("user_id", rider_id).execute()


def get_dashboard_summary(rider_id: str) -> dict[str, Any]:
    sb = _sb()
    prof = get_rider_profile(rider_id) or {}
    jobs = sb.table("rider_jobs").select("id, status, job_type, completed_at").eq(
        "rider_id", rider_id
    ).execute()
    all_jobs = jobs.data or []
    today = datetime.now(timezone.utc).date().isoformat()
    active = [j for j in all_jobs if j["status"] not in ("completed", "cancelled", "rejected", "available")]
    completed = [j for j in all_jobs if j["status"] == "completed"]
    today_jobs = [j for j in completed if (j.get("completed_at") or "")[:10] == today]

    earnings = sb.table("rider_earnings").select("amount, created_at").eq("rider_id", rider_id).execute()
    rows = earnings.data or []
    total_today = sum(
        float(r["amount"])
        for r in rows
        if (r.get("created_at") or "")[:10] == today
    )
    week_start = datetime.now(timezone.utc).date().toordinal() - 6
    month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")

    def in_week(iso: str) -> bool:
        try:
            d = datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
            return d.toordinal() >= week_start
        except Exception:
            return False

    week_earn = sum(float(r["amount"]) for r in rows if in_week(r.get("created_at") or ""))
    month_earn = sum(
        float(r["amount"]) for r in rows if (r.get("created_at") or "").startswith(month_prefix)
    )

    return {
        "profile": prof,
        "today_jobs": len(today_jobs),
        "active_jobs": len(active),
        "completed_jobs": len(completed),
        "today_earnings": total_today,
        "week_earnings": week_earn,
        "month_earnings": month_earn,
        "wallet_balance": float(prof.get("wallet_balance") or 0),
        "lifetime_earnings": float(prof.get("lifetime_earnings") or 0),
        "avg_rating": float(prof.get("avg_rating") or 0),
        "is_online": bool(prof.get("is_online")),
    }


def get_earnings_history(rider_id: str) -> list[dict[str, Any]]:
    res = (
        _sb()
        .table("rider_earnings")
        .select("*")
        .eq("rider_id", rider_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


def submit_rider_review(
    *,
    rider_id: str,
    reviewer_id: str,
    reviewer_role: str,
    confirmed_order_id: str,
    rating: int,
    comment: str | None,
) -> dict[str, Any]:
    if rating < 1 or rating > 5:
        raise ValueError("Rating must be 1-5")
    sb = _sb()
    sb.table("rider_reviews").insert(
        {
            "rider_id": rider_id,
            "reviewer_id": reviewer_id,
            "reviewer_role": reviewer_role,
            "confirmed_order_id": confirmed_order_id,
            "rating": rating,
            "comment": comment or "",
        }
    ).execute()
    reviews = (
        sb.table("rider_reviews").select("rating, reviewer_role").eq("rider_id", rider_id).execute()
    )
    rows = reviews.data or []
    if not rows:
        return {"ok": True}
    avg = sum(int(r["rating"]) for r in rows) / len(rows)
    cust = sum(1 for r in rows if r.get("reviewer_role") == "customer")
    tail = sum(1 for r in rows if r.get("reviewer_role") == "tailor")
    sb.table("rider_profiles").update(
        {
            "avg_rating": round(avg, 2),
            "total_reviews": len(rows),
            "customer_reviews": cust,
            "tailor_reviews": tail,
            "updated_at": _now(),
        }
    ).eq("user_id", rider_id).execute()
    _notify_rider(sb, rider_id, "review_received", "New review", f"You received a {rating}-star review")
    return {"ok": True, "avg_rating": round(avg, 2)}


def get_job_for_order(confirmed_order_id: str) -> list[dict[str, Any]]:
    res = (
        _sb()
        .table("rider_jobs")
        .select("*")
        .eq("confirmed_order_id", confirmed_order_id)
        .order("created_at")
        .execute()
    )
    jobs = res.data or []
    for job in jobs:
        if job.get("rider_id"):
            job["rider_profile"] = get_rider_profile(str(job["rider_id"]))
    return jobs


def get_rider_on_job(job_id: str) -> dict[str, Any] | None:
    job = _sb().table("rider_jobs").select("*").eq("id", job_id).limit(1).execute()
    row = (job.data or [None])[0]
    if not row or not row.get("rider_id"):
        return None
    prof = get_rider_profile(row["rider_id"])
    return {**row, "rider_profile": prof}
