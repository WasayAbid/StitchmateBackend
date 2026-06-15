"""Rider portal API — shifts, jobs, earnings, reviews."""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_current_user_id
from app.services.rider_store import (
    DRESS_DELIVERY_REWARD,
    FABRIC_PICKUP_REWARD,
    accept_job,
    create_fabric_pickup_job,
    end_shift,
    get_dashboard_summary,
    get_earnings_history,
    get_job_for_order,
    get_rider_profile,
    list_available_jobs,
    list_rider_jobs,
    reject_job,
    start_shift,
    submit_rider_review,
    tailor_confirm_fabric,
    tailor_ready_for_pickup,
    update_job_status,
    upsert_rider_profile,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/riders", tags=["riders"])


class RiderProfileReq(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    cnic: str | None = None
    address: str | None = None
    city: str | None = None
    bike_number: str | None = None
    bike_registration: str | None = None
    bike_model: str | None = None
    profile_image_url: str | None = None
    cnic_front_url: str | None = None
    cnic_back_url: str | None = None


class JobStatusReq(BaseModel):
    status: str


class ReviewReq(BaseModel):
    rider_id: str
    confirmed_order_id: str
    rating: int = Field(ge=1, le=5)
    comment: str | None = None
    reviewer_role: str  # customer | tailor


class RegisterCompleteReq(BaseModel):
    full_name: str
    phone: str
    email: str
    cnic: str
    address: str
    city: str
    bike_number: str
    bike_registration: str
    bike_model: str
    profile_image_url: str | None = None
    cnic_front_url: str | None = None
    cnic_back_url: str | None = None


@router.get("/rewards")
def get_reward_config():
    return {
        "fabric_pickup_reward": FABRIC_PICKUP_REWARD,
        "dress_delivery_reward": DRESS_DELIVERY_REWARD,
    }


@router.get("/profile")
def rider_profile(authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    prof = get_rider_profile(rider_id)
    return {"profile": prof}


@router.put("/profile")
def update_profile(body: RiderProfileReq, authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    patch = body.model_dump(exclude_none=True)
    if "cnic" in patch:
        existing = get_rider_profile(rider_id)
        if existing and existing.get("cnic") and patch["cnic"] != existing.get("cnic"):
            raise HTTPException(status_code=400, detail="CNIC cannot be changed")
    return {"profile": upsert_rider_profile(rider_id, patch)}


@router.post("/register/complete")
def complete_registration(body: RegisterCompleteReq, authorization: str | None = Header(None)):
    """Finalize rider profile after OTP — immediate access."""
    rider_id = get_current_user_id(authorization)
    from supabase_client import supabase

    try:
        supabase.rpc("assign_rider_role", {"_user_id": rider_id}).execute()
    except Exception as exc:
        logger.warning("assign_rider_role: %s", exc)
        try:
            supabase.table("user_roles").upsert(
                {"user_id": rider_id, "role": "rider"}, on_conflict="user_id,role"
            ).execute()
        except Exception:
            pass

    prof = upsert_rider_profile(
        rider_id,
        {
            "full_name": body.full_name,
            "phone": body.phone,
            "email": body.email,
            "cnic": body.cnic,
            "address": body.address,
            "city": body.city,
            "bike_number": body.bike_number,
            "bike_registration": body.bike_registration,
            "bike_model": body.bike_model,
            "profile_image_url": body.profile_image_url,
            "cnic_front_url": body.cnic_front_url,
            "cnic_back_url": body.cnic_back_url,
        },
    )
    supabase.table("profiles").upsert(
        {"user_id": rider_id, "full_name": body.full_name, "phone": body.phone},
        on_conflict="user_id",
    ).execute()
    return {"ok": True, "profile": prof}


@router.post("/shift/start")
def shift_start(authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    return {"profile": start_shift(rider_id)}


@router.post("/shift/end")
def shift_end(authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    return {"profile": end_shift(rider_id)}


@router.get("/dashboard")
def dashboard(authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    return get_dashboard_summary(rider_id)


@router.get("/jobs/available")
def jobs_available(authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    return {"jobs": list_available_jobs(rider_id)}


@router.get("/jobs")
def my_jobs(active_only: bool = False, authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    return {"jobs": list_rider_jobs(rider_id, active_only=active_only)}


@router.get("/jobs/history")
def job_history(authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    jobs = list_rider_jobs(rider_id, active_only=False)
    completed = [j for j in jobs if j.get("status") == "completed"]
    return {"jobs": completed}


@router.post("/jobs/{job_id}/accept")
def job_accept(job_id: str, authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    try:
        return {"job": accept_job(job_id, rider_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/reject")
def job_reject(job_id: str, authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    return reject_job(job_id, rider_id)


@router.patch("/jobs/{job_id}/status")
def job_status(job_id: str, body: JobStatusReq, authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    try:
        return {"job": update_job_status(job_id, rider_id, body.status)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/earnings")
def earnings(authorization: str | None = Header(None)):
    rider_id = get_current_user_id(authorization)
    summary = get_dashboard_summary(rider_id)
    history = get_earnings_history(rider_id)
    return {"summary": summary, "history": history}


@router.post("/orders/{confirmed_order_id}/fabric-job")
def trigger_fabric_job(confirmed_order_id: str, authorization: str | None = Header(None)):
    get_current_user_id(authorization)
    job = create_fabric_pickup_job(confirmed_order_id)
    if not job:
        raise HTTPException(status_code=400, detail="Fabric pickup not required for this order")
    return {"job": job}


@router.post("/tailor/orders/{confirmed_order_id}/confirm-fabric")
def tailor_confirm(confirmed_order_id: str, authorization: str | None = Header(None)):
    tailor_id = get_current_user_id(authorization)
    try:
        return {"order": tailor_confirm_fabric(confirmed_order_id, tailor_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tailor/orders/{confirmed_order_id}/ready-for-pickup")
def tailor_ready(confirmed_order_id: str, authorization: str | None = Header(None)):
    tailor_id = get_current_user_id(authorization)
    try:
        return {"order": tailor_ready_for_pickup(confirmed_order_id, tailor_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/orders/{confirmed_order_id}/tracking")
def order_tracking(confirmed_order_id: str, authorization: str | None = Header(None)):
    get_current_user_id(authorization)
    jobs = get_job_for_order(confirmed_order_id)
    riders: list[dict[str, Any]] = []
    for j in jobs:
        if j.get("rider_id"):
            prof = get_rider_profile(str(j["rider_id"]))
            riders.append({"job": j, "rider": prof})
    return {"jobs": jobs, "riders": riders}


@router.post("/reviews")
def post_review(body: ReviewReq, authorization: str | None = Header(None)):
    reviewer_id = get_current_user_id(authorization)
    if body.reviewer_role not in ("customer", "tailor"):
        raise HTTPException(status_code=400, detail="reviewer_role must be customer or tailor")
    try:
        return submit_rider_review(
            rider_id=body.rider_id,
            reviewer_id=reviewer_id,
            reviewer_role=body.reviewer_role,
            confirmed_order_id=body.confirmed_order_id,
            rating=body.rating,
            comment=body.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reviews/{rider_id}")
def get_reviews(rider_id: str, authorization: str | None = Header(None)):
    get_current_user_id(authorization)
    from supabase_client import supabase

    res = (
        supabase.table("rider_reviews")
        .select("*")
        .eq("rider_id", rider_id)
        .order("created_at", desc=True)
        .execute()
    )
    prof = get_rider_profile(rider_id)
    return {"reviews": res.data or [], "profile": prof}
