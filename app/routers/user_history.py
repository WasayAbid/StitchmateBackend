"""User history — log and list creative AI activities."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.deps import get_current_user_id
from app.services.user_history_store import (
    delete_user_history,
    insert_user_history,
    list_user_history,
    update_user_history,
)

router = APIRouter(prefix="/api", tags=["user-history"])


class HistoryLogRequest(BaseModel):
    activity_type: str = Field(
        ...,
        description="design_studio | virtual_try_on | accessory_overlay",
    )
    input_details: dict[str, Any] = Field(default_factory=dict)
    output_details: dict[str, Any] = Field(default_factory=dict)
    related_resource_id: Optional[str] = None


@router.post("/user-history")
async def log_user_history(
    body: HistoryLogRequest,
    user_id: str = Depends(get_current_user_id),
):
    try:
        row = insert_user_history(
            user_id=user_id,
            activity_type=body.activity_type,
            input_details=body.input_details,
            output_details=body.output_details,
            related_resource_id=body.related_resource_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    return {"ok": True, "entry": row}


@router.get("/user-history")
async def get_user_history(
    user_id: str = Depends(get_current_user_id),
    activity_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    try:
        rows = list_user_history(
            user_id=user_id,
            activity_type=activity_type,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    return {"items": rows, "count": len(rows)}


class HistoryUpdateRequest(BaseModel):
    input_details: Optional[dict[str, Any]] = None
    output_details: Optional[dict[str, Any]] = None


@router.patch("/user-history/{entry_id}")
async def patch_user_history(
    entry_id: str,
    body: HistoryUpdateRequest,
    user_id: str = Depends(get_current_user_id),
):
    if body.input_details is None and body.output_details is None:
        raise HTTPException(400, "Provide input_details and/or output_details to update")
    try:
        row = update_user_history(
            user_id=user_id,
            entry_id=entry_id,
            input_details=body.input_details,
            output_details=body.output_details,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    if not row:
        raise HTTPException(404, "History entry not found")
    return {"ok": True, "entry": row}


@router.delete("/user-history/{entry_id}")
async def remove_user_history(
    entry_id: str,
    user_id: str = Depends(get_current_user_id),
):
    try:
        ok = delete_user_history(user_id=user_id, entry_id=entry_id)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    if not ok:
        raise HTTPException(404, "History entry not found")
    return {"ok": True, "deleted": entry_id}
