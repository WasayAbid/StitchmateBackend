"""Stripe Checkout helpers."""
from __future__ import annotations

import os
from typing import Any

import stripe

from app.services.payments_store import CREDIT_PLANS

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")


def _currency() -> str:
    return os.environ.get("STRIPE_CURRENCY", "usd").lower()


def _price_id(plan_key: str) -> str:
    env_map = {
        "one_day": "STRIPE_PRICE_ONE_DAY",
        "one_week": "STRIPE_PRICE_ONE_WEEK",
        "fifteen_day": "STRIPE_PRICE_FIFTEEN_DAY",
        "monthly": "STRIPE_PRICE_MONTHLY",
    }
    env_name = env_map.get(plan_key)
    if not env_name:
        raise ValueError(f"Unknown plan: {plan_key}")
    price_id = os.environ.get(env_name, "").strip()
    if not price_id:
        raise ValueError(
            f"Missing {env_name} in backend .env — create a Price in Stripe Dashboard first."
        )
    return price_id


def create_credits_checkout(*, user_id: str, user_email: str | None, plan_key: str) -> dict[str, str]:
    plan = CREDIT_PLANS.get(plan_key)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_key}")

    price_id = _price_id(plan_key)
    mode = plan["mode"]
    success = f"{_frontend_url()}/dashboard/payment-success?type=credits&plan={plan_key}"
    cancel = f"{_frontend_url()}/dashboard?payment=cancelled"

    session_params: dict[str, Any] = {
        "mode": mode,
        "success_url": success + "&session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": cancel,
        "client_reference_id": user_id,
        "metadata": {
            "payment_type": "ai_credits",
            "plan_key": plan_key,
            "user_id": user_id,
            "credits": str(plan["credits"]),
        },
        "line_items": [{"price": price_id, "quantity": 1}],
    }
    if user_email:
        session_params["customer_email"] = user_email

    session = stripe.checkout.Session.create(**session_params)
    return {"checkout_url": session.url or "", "session_id": session.id}


def create_order_checkout(
    *,
    user_id: str,
    user_email: str | None,
    confirmed_order_id: str,
    amount: float,
    tailor_name: str | None,
) -> dict[str, str]:
    """One-time Stripe Checkout for order balance (pay after stitching complete)."""
    currency = _currency()
    # Stripe amounts are in smallest currency unit (cents/paisa)
    unit_amount = int(round(float(amount) * 100))
    if unit_amount < 50:
        raise ValueError("Order amount too small for Stripe checkout")

    success = (
        f"{_frontend_url()}/dashboard/payment-success"
        f"?type=order&confirmed_order_id={confirmed_order_id}"
    )
    cancel = f"{_frontend_url()}/dashboard/order-payment?confirmedOrderId={confirmed_order_id}"

    session_params: dict[str, Any] = {
        "mode": "payment",
        "success_url": success + "&session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": cancel,
        "client_reference_id": user_id,
        "metadata": {
            "payment_type": "order",
            "confirmed_order_id": confirmed_order_id,
            "user_id": user_id,
        },
        "line_items": [
            {
                "price_data": {
                    "currency": currency,
                    "unit_amount": unit_amount,
                    "product_data": {
                        "name": f"StitchMate Order — {tailor_name or 'Tailoring'}",
                        "description": "Payment after stitching completed",
                    },
                },
                "quantity": 1,
            }
        ],
    }
    if user_email:
        session_params["customer_email"] = user_email

    session = stripe.checkout.Session.create(**session_params)
    return {"checkout_url": session.url or "", "session_id": session.id}


def find_paid_checkout_for_order(confirmed_order_id: str):
    """Find a completed Stripe Checkout session for this confirmed order (if webhook was missed)."""
    matches = []
    starting_after = None
    for _ in range(4):
        params: dict = {"limit": 100}
        if starting_after:
            params["starting_after"] = starting_after
        result = stripe.checkout.Session.list(**params)
        for session in result.data:
            data = session.to_dict()
            meta = data.get("metadata") or {}
            if (
                meta.get("confirmed_order_id") == confirmed_order_id
                and meta.get("payment_type") == "order"
                and data.get("payment_status") == "paid"
            ):
                matches.append(session)
        if not result.has_more or matches:
            break
        starting_after = result.data[-1].id
    result.data = matches
    return result
