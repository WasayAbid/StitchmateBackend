"""Supabase persistence for payments and AI credits."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from supabase import Client, create_client

logger = logging.getLogger(__name__)

FREE_TIER_CREDITS = 3

CREDIT_PLANS: dict[str, dict[str, Any]] = {
    "one_day": {"label": "One-Day Trial", "credits": 20, "days": 1, "mode": "payment"},
    "one_week": {"label": "One-Week Plan", "credits": 80, "days": 7, "mode": "payment"},
    "fifteen_day": {"label": "Fifteen-Day Plan", "credits": 180, "days": 15, "mode": "payment"},
    "monthly": {"label": "Monthly Plan", "credits": 400, "days": 30, "mode": "subscription"},
}


def _sb() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def get_or_create_credits(user_id: str) -> dict[str, Any]:
    sb = _sb()
    row = sb.table("user_ai_credits").select("*").eq("user_id", user_id).limit(1).execute()
    if row.data:
        return row.data[0]
    created = sb.table("user_ai_credits").insert(
        {
            "user_id": user_id,
            "credits_balance": FREE_TIER_CREDITS,
            "plan_id": "free",
            "plan_label": "Free Tier",
        }
    ).execute()
    return (created.data or [{}])[0]


def grant_credits(
    *,
    user_id: str,
    credits: int,
    plan_id: str,
    plan_label: str,
    days: int,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    stripe_session_id: str | None = None,
    reason: str = "purchase",
) -> dict[str, Any]:
    sb = _sb()
    current = get_or_create_credits(user_id)
    new_balance = int(current.get("credits_balance") or 0) + credits
    expires = datetime.now(timezone.utc) + timedelta(days=days)

    patch: dict[str, Any] = {
        "credits_balance": new_balance,
        "plan_id": plan_id,
        "plan_label": plan_label,
        "plan_expires_at": expires.isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if stripe_customer_id:
        patch["stripe_customer_id"] = stripe_customer_id
    if stripe_subscription_id:
        patch["stripe_subscription_id"] = stripe_subscription_id

    sb.table("user_ai_credits").upsert({"user_id": user_id, **patch}).execute()
    sb.table("ai_credit_transactions").insert(
        {
            "user_id": user_id,
            "delta": credits,
            "balance_after": new_balance,
            "reason": reason,
            "stripe_session_id": stripe_session_id,
        }
    ).execute()
    return {**current, **patch, "credits_balance": new_balance}


def payment_exists_for_session(stripe_session_id: str) -> bool:
    sb = _sb()
    res = (
        sb.table("payments")
        .select("id")
        .eq("stripe_session_id", stripe_session_id)
        .limit(1)
        .execute()
    )
    return bool(res.data)


def record_payment(
    *,
    user_id: str,
    payment_type: str,
    amount: float | None,
    currency: str,
    status: str,
    payment_method: str | None = None,
    confirmed_order_id: str | None = None,
    stripe_session_id: str | None = None,
    stripe_payment_intent: str | None = None,
    metadata: dict | None = None,
) -> dict[str, Any]:
    sb = _sb()
    row = {
        "user_id": user_id,
        "payment_type": payment_type,
        "amount": amount,
        "currency": currency,
        "status": status,
        "payment_method": payment_method,
        "confirmed_order_id": confirmed_order_id,
        "stripe_session_id": stripe_session_id,
        "stripe_payment_intent": stripe_payment_intent,
        "metadata": metadata or {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = sb.table("payments").insert(row).execute()
    return (res.data or [row])[0]


def get_confirmed_order(order_id: str) -> dict[str, Any] | None:
    sb = _sb()
    res = sb.table("confirmed_orders").select("*").eq("id", order_id).limit(1).execute()
    return (res.data or [None])[0]


def mark_order_paid_stripe(
    confirmed_order_id: str,
    *,
    stripe_session_id: str,
    stripe_payment_intent: str | None = None,
) -> None:
    sb = _sb()
    now = datetime.now(timezone.utc).isoformat()
    sb.table("confirmed_orders").update(
        {
            "payment_status": "paid",
            "stripe_session_id": stripe_session_id,
            "updated_at": now,
        }
    ).eq("id", confirmed_order_id).execute()

    order = get_confirmed_order(confirmed_order_id)
    if order and order.get("order_id"):
        sb.table("orders").update({"status": "completed", "updated_at": now}).eq(
            "id", order["order_id"]
        ).execute()


def mark_order_cod_confirmed(confirmed_order_id: str, tailor_id: str) -> dict[str, Any]:
    sb = _sb()
    order = get_confirmed_order(confirmed_order_id)
    if not order:
        raise ValueError("Order not found")
    if str(order.get("tailor_id")) != str(tailor_id):
        raise ValueError("Only the assigned tailor can confirm COD")
    if order.get("payment_method") != "cod":
        raise ValueError("This order is not cash-on-delivery")
    if order.get("status") != "completed":
        raise ValueError("Stitching must be marked complete before COD confirmation")

    now = datetime.now(timezone.utc).isoformat()
    sb.table("confirmed_orders").update(
        {
            "payment_status": "paid_cod",
            "cod_confirmed_at": now,
            "updated_at": now,
        }
    ).eq("id", confirmed_order_id).execute()

    record_payment(
        user_id=order["user_id"],
        payment_type="order",
        amount=float(order.get("final_amount") or 0),
        currency=os.environ.get("STRIPE_CURRENCY", "usd"),
        status="paid",
        payment_method="cod",
        confirmed_order_id=confirmed_order_id,
        metadata={"confirmed_by_tailor": tailor_id},
    )
    return get_confirmed_order(confirmed_order_id) or {}


def update_delivery_status(
    confirmed_order_id: str,
    tailor_id: str,
    delivery_status: str,
) -> dict[str, Any]:
    sb = _sb()
    order = get_confirmed_order(confirmed_order_id)
    if not order:
        raise ValueError("Order not found")
    if str(order.get("tailor_id")) != str(tailor_id):
        raise ValueError("Only the assigned tailor can update delivery")

    allowed = {"ready_for_pickup", "out_for_delivery", "delivered", "picked_up"}
    if delivery_status not in allowed:
        raise ValueError(f"Invalid delivery_status. Use one of: {', '.join(sorted(allowed))}")

    now = datetime.now(timezone.utc).isoformat()
    patch: dict[str, Any] = {
        "delivery_status": delivery_status,
        "delivery_confirmed_by": tailor_id,
        "updated_at": now,
    }
    if delivery_status in ("delivered", "picked_up"):
        patch["delivery_confirmed_at"] = now
        patch["status"] = "delivered"

    sb.table("confirmed_orders").update(patch).eq("id", confirmed_order_id).execute()
    if order.get("order_id") and delivery_status in ("delivered", "picked_up"):
        sb.table("orders").update({"status": "completed", "updated_at": now}).eq(
            "id", order["order_id"]
        ).execute()
    return get_confirmed_order(confirmed_order_id) or {}


def complete_stitching(confirmed_order_id: str, tailor_id: str) -> dict[str, Any]:
    sb = _sb()
    order = get_confirmed_order(confirmed_order_id)
    if not order:
        raise ValueError("Order not found")
    if str(order.get("tailor_id")) != str(tailor_id):
        raise ValueError("Only the assigned tailor can mark stitching complete")

    now = datetime.now(timezone.utc).isoformat()
    logistics = order.get("logistics_type") or "home_pickup"
    delivery_status = "ready_for_pickup" if logistics == "self_drop" else "pending"

    sb.table("confirmed_orders").update(
        {
            "status": "completed",
            "stitching_completed_at": now,
            "delivery_status": delivery_status,
            "updated_at": now,
        }
    ).eq("id", confirmed_order_id).execute()

    if order.get("order_id"):
        sb.table("orders").update({"status": "completed", "updated_at": now}).eq(
            "id", order["order_id"]
        ).execute()

    _notify_stitching_complete(sb, order, confirmed_order_id)

    return get_confirmed_order(confirmed_order_id) or {}


def _notify_stitching_complete(sb: Client, order: dict[str, Any], confirmed_order_id: str) -> None:
    """Notify customer that stitching is done and payment can proceed."""
    user_id = order.get("user_id")
    if not user_id:
        return
    design = order.get("selected_design_names") or "Your garment"
    pay_method = order.get("payment_method") or "stripe"
    message = (
        f"{design} is stitched and ready! Proceed to payment."
        if pay_method == "stripe"
        else f"{design} is stitched and ready! Pay cash on delivery/pickup."
    )
    try:
        sb.table("user_notifications").insert(
            {
                "user_id": user_id,
                "type": "order_ready_for_payment",
                "title": "Your order is stitched!",
                "message": message,
                "confirmed_order_id": confirmed_order_id,
                "read": False,
            }
        ).execute()
    except Exception as exc:
        logger.warning("Could not create user notification: %s", exc)
