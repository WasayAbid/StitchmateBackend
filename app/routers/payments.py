"""Stripe payments: AI credits + order pay-on-completion."""
from __future__ import annotations

import logging
import os

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.deps import get_current_user_id
from app.services.payments_store import (
    CREDIT_PLANS,
    complete_stitching,
    get_confirmed_order,
    get_or_create_credits,
    grant_credits,
    mark_order_cod_confirmed,
    mark_order_paid_stripe,
    payment_exists_for_session,
    record_payment,
    update_delivery_status,
)
from app.services.stripe_service import (
    create_credits_checkout,
    create_order_checkout,
    find_paid_checkout_for_order,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


class CreditsCheckoutReq(BaseModel):
    plan_key: str  # one_day | one_week | fifteen_day | monthly


class OrderCheckoutReq(BaseModel):
    confirmed_order_id: str


class ConfirmCheckoutReq(BaseModel):
    session_id: str


def _checkout_fields(session) -> dict:
    """Normalize Stripe Checkout Session (StripeObject or dict) to plain fields."""
    data = session.to_dict() if hasattr(session, "to_dict") else dict(session)
    meta = data.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "meta": meta,
        "user_id": meta.get("user_id") or data.get("client_reference_id"),
        "session_id": data.get("id"),
        "payment_intent": data.get("payment_intent"),
        "amount_total": data.get("amount_total") or 0,
        "currency": data.get("currency") or "usd",
        "customer": data.get("customer"),
        "subscription": data.get("subscription"),
    }


def _process_checkout_completed(session) -> None:
    """Apply DB updates for a paid Stripe Checkout session (webhook + success-page fallback)."""
    fields = _checkout_fields(session)
    meta = fields["meta"]
    payment_type = meta.get("payment_type")
    user_id = fields["user_id"]
    session_id = fields["session_id"]
    payment_intent = fields["payment_intent"]
    amount_total = fields["amount_total"]
    currency = fields["currency"]
    already_recorded = payment_exists_for_session(session_id)

    if payment_type == "ai_credits" and user_id:
        if not already_recorded:
            plan_key = meta.get("plan_key", "one_week")
            plan = CREDIT_PLANS.get(plan_key, CREDIT_PLANS["one_week"])
            grant_credits(
                user_id=user_id,
                credits=plan["credits"],
                plan_id=plan_key,
                plan_label=plan["label"],
                days=plan["days"],
                stripe_customer_id=fields["customer"],
                stripe_subscription_id=fields["subscription"],
                stripe_session_id=session_id,
            )
            record_payment(
                user_id=user_id,
                payment_type="ai_credits",
                amount=amount_total / 100,
                currency=currency,
                status="paid",
                payment_method="stripe",
                stripe_session_id=session_id,
                stripe_payment_intent=payment_intent,
                metadata={"plan_key": plan_key},
            )

    elif payment_type == "order" and user_id:
        confirmed_order_id = meta.get("confirmed_order_id")
        if confirmed_order_id:
            order = get_confirmed_order(confirmed_order_id)
            if order and order.get("payment_status") not in ("paid", "paid_cod"):
                mark_order_paid_stripe(
                    confirmed_order_id,
                    stripe_session_id=session_id,
                    stripe_payment_intent=payment_intent,
                )
            if not already_recorded:
                record_payment(
                    user_id=user_id,
                    payment_type="order",
                    amount=amount_total / 100,
                    currency=currency,
                    status="paid",
                    payment_method="stripe",
                    confirmed_order_id=confirmed_order_id,
                    stripe_session_id=session_id,
                    stripe_payment_intent=payment_intent,
                )


class DeliveryUpdateReq(BaseModel):
    delivery_status: str  # ready_for_pickup | out_for_delivery | delivered | picked_up


@router.get("/credits")
def get_credits(authorization: str | None = Header(None)):
    user_id = get_current_user_id(authorization)
    try:
        row = get_or_create_credits(user_id)
        return {
            "credits": int(row.get("credits_balance") or 0),
            "plan_id": row.get("plan_id") or "free",
            "plan_label": row.get("plan_label") or "Free Tier",
            "plan_expires_at": row.get("plan_expires_at"),
            "plans": [
                {
                    "key": k,
                    "label": v["label"],
                    "credits": v["credits"],
                    "mode": v["mode"],
                }
                for k, v in CREDIT_PLANS.items()
            ],
        }
    except Exception as exc:
        logger.exception("get_credits failed")
        raise HTTPException(500, str(exc)) from exc


@router.post("/credits/checkout")
def credits_checkout(body: CreditsCheckoutReq, authorization: str | None = Header(None)):
    user_id = get_current_user_id(authorization)
    if body.plan_key not in CREDIT_PLANS:
        raise HTTPException(400, f"Invalid plan_key. Choose: {', '.join(CREDIT_PLANS)}")

    if not os.environ.get("STRIPE_SECRET_KEY"):
        raise HTTPException(503, "Stripe is not configured on the server.")

    try:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from supabase_client import supabase

        user_resp = supabase.auth.get_user(authorization.split(" ", 1)[1])
        email = user_resp.user.email if user_resp and user_resp.user else None
    except Exception:
        email = None

    try:
        session = create_credits_checkout(user_id=user_id, user_email=email, plan_key=body.plan_key)
        return session
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except stripe.error.StripeError as exc:
        raise HTTPException(502, f"Stripe error: {exc.user_message or str(exc)}") from exc


@router.post("/order/checkout")
def order_checkout(body: OrderCheckoutReq, authorization: str | None = Header(None)):
    user_id = get_current_user_id(authorization)
    if not os.environ.get("STRIPE_SECRET_KEY"):
        raise HTTPException(503, "Stripe is not configured on the server.")

    order = get_confirmed_order(body.confirmed_order_id)
    if not order:
        raise HTTPException(404, "Confirmed order not found")
    if str(order.get("user_id")) != user_id:
        raise HTTPException(403, "Not your order")
    if order.get("status") != "completed":
        raise HTTPException(400, "Payment is available after tailor marks stitching complete.")
    if order.get("payment_method") != "stripe":
        raise HTTPException(400, "This order uses cash on delivery, not online payment.")
    if order.get("payment_status") in ("paid", "paid_cod"):
        raise HTTPException(400, "This order is already paid.")

    # Stripe may already have a paid session (user saw "all done here") — sync DB first
    try:
        paid_sessions = find_paid_checkout_for_order(body.confirmed_order_id)
        if paid_sessions.data:
            _process_checkout_completed(paid_sessions.data[0])
            refreshed = get_confirmed_order(body.confirmed_order_id)
            if refreshed and refreshed.get("payment_status") in ("paid", "paid_cod"):
                return {
                    "already_paid": True,
                    "payment_status": refreshed.get("payment_status"),
                    "message": "Payment was already completed on Stripe — database updated.",
                }
    except Exception:
        logger.exception("order_checkout stripe sync pre-check failed")

    try:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from supabase_client import supabase

        user_resp = supabase.auth.get_user(authorization.split(" ", 1)[1])
        email = user_resp.user.email if user_resp and user_resp.user else None
    except Exception:
        email = None

    try:
        session = create_order_checkout(
            user_id=user_id,
            user_email=email,
            confirmed_order_id=body.confirmed_order_id,
            amount=float(order.get("final_amount") or 0),
            tailor_name=order.get("tailor_shop_name") or order.get("tailor_name"),
        )
        return session
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except stripe.error.StripeError as exc:
        raise HTTPException(502, f"Stripe error: {exc.user_message or str(exc)}") from exc


@router.post("/order/{confirmed_order_id}/complete-stitching")
def tailor_complete_stitching(confirmed_order_id: str, authorization: str | None = Header(None)):
    tailor_id = get_current_user_id(authorization)
    try:
        order = complete_stitching(confirmed_order_id, tailor_id)
        return {"ok": True, "order": order}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/order/{confirmed_order_id}/confirm-cod")
def tailor_confirm_cod(confirmed_order_id: str, authorization: str | None = Header(None)):
    tailor_id = get_current_user_id(authorization)
    try:
        order = mark_order_cod_confirmed(confirmed_order_id, tailor_id)
        return {"ok": True, "order": order}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/order/{confirmed_order_id}/delivery")
def tailor_update_delivery(
    confirmed_order_id: str,
    body: DeliveryUpdateReq,
    authorization: str | None = Header(None),
):
    tailor_id = get_current_user_id(authorization)
    try:
        order = update_delivery_status(confirmed_order_id, tailor_id, body.delivery_status)
        return {"ok": True, "order": order}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/order/{confirmed_order_id}/sync-payment")
def sync_order_payment(confirmed_order_id: str, authorization: str | None = Header(None)):
    """Pull paid Stripe checkout into DB when webhook/success redirect was missed."""
    user_id = get_current_user_id(authorization)
    if not os.environ.get("STRIPE_SECRET_KEY"):
        raise HTTPException(503, "Stripe is not configured on the server.")

    order = get_confirmed_order(confirmed_order_id)
    if not order:
        raise HTTPException(404, "Confirmed order not found")
    if str(order.get("user_id")) != user_id:
        raise HTTPException(403, "Not your order")
    if order.get("payment_method") != "stripe":
        raise HTTPException(400, "This order does not use online payment.")
    if order.get("payment_status") in ("paid", "paid_cod"):
        return {"ok": True, "payment_status": order.get("payment_status"), "synced": False}

    try:
        paid_sessions = find_paid_checkout_for_order(confirmed_order_id)
    except stripe.error.StripeError as exc:
        raise HTTPException(502, f"Stripe error: {exc.user_message or str(exc)}") from exc

    if not paid_sessions.data:
        raise HTTPException(
            404,
            "No completed Stripe payment found for this order. Start a new checkout if you have not paid yet.",
        )

    session = paid_sessions.data[0]
    fields = _checkout_fields(session)
    if str(fields["user_id"]) != str(user_id):
        raise HTTPException(403, "Stripe payment does not match your account.")

    try:
        _process_checkout_completed(session)
    except Exception as exc:
        logger.exception("sync_order_payment failed")
        raise HTTPException(500, str(exc)) from exc

    refreshed = get_confirmed_order(confirmed_order_id)
    return {
        "ok": True,
        "synced": True,
        "payment_status": refreshed.get("payment_status") if refreshed else "paid",
    }


@router.post("/checkout/confirm")
def confirm_checkout(body: ConfirmCheckoutReq, authorization: str | None = Header(None)):
    """Verify Stripe session after redirect — updates DB if webhook was missed (common in local dev)."""
    user_id = get_current_user_id(authorization)
    if not os.environ.get("STRIPE_SECRET_KEY"):
        raise HTTPException(503, "Stripe is not configured on the server.")

    try:
        session = stripe.checkout.Session.retrieve(body.session_id)
    except stripe.error.StripeError as exc:
        raise HTTPException(400, f"Stripe error: {exc.user_message or str(exc)}") from exc

    if session.payment_status != "paid":
        raise HTTPException(400, "Payment not completed yet.")

    fields = _checkout_fields(session)
    owner_id = fields["user_id"]
    if str(owner_id) != str(user_id):
        raise HTTPException(403, "This payment session does not belong to you.")

    try:
        _process_checkout_completed(session)
    except Exception as exc:
        logger.exception("confirm_checkout failed")
        raise HTTPException(500, str(exc)) from exc

    return {"ok": True, "payment_status": "paid"}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    if not secret:
        raise HTTPException(503, "STRIPE_WEBHOOK_SECRET not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except ValueError as exc:
        raise HTTPException(400, "Invalid payload") from exc
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(400, "Invalid signature") from exc

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        try:
            _process_checkout_completed(session)
        except Exception:
            logger.exception("stripe_webhook checkout.session.completed failed")
            raise

    return {"received": True}
