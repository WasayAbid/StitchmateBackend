import json
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user_id
from app.models import (
    DesignPreference,
    FabricBatch,
    FabricPiece,
    FeasibilityRecord,
    GeneratedDesignRecord,
)
from app.services.design_descriptions import DESIGN_DESCRIPTIONS
from app.services.fabric_storage import public_url, save_upload
from app.services.gemini_tailor import (
    check_feasibility_simple,
    run_design_generation_spec,
    run_feasibility_analysis,
)

router = APIRouter(prefix="/api", tags=["workflow"])


def _load_fabric_context(db: Session, user_id: str, batch_id: str | None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    q = db.query(FabricBatch).options(selectinload(FabricBatch.pieces))
    if batch_id:
        b = q.filter(FabricBatch.id == batch_id, FabricBatch.user_id == user_id).first()
        if not b:
            raise HTTPException(404, "Fabric batch not found")
        batches = [b]
    else:
        batches = q.filter(FabricBatch.user_id == user_id).order_by(FabricBatch.created_at.desc()).limit(1).all()
        if not batches:
            raise HTTPException(400, "No fabric uploads found. Upload fabric first.")
        b = batches[0]

    pieces = sorted(b.pieces, key=lambda p: (p.upload_timestamp.isoformat() if p.upload_timestamp else "", p.id))
    fabric_list = []
    for p in pieces:
        fabric_list.append(
            {
                "label": p.label,
                "length": p.length,
                "width": p.width,
                "unit": p.unit,
                "notes": p.notes,
                "gemini_suggested_label": p.gemini_suggested_label,
                "image_url": public_url(p.image_path),
            }
        )
    overall = {
        "length": b.overall_length,
        "width": b.overall_width,
        "unit": b.overall_unit,
        "notes": b.overall_notes,
    }
    if not any(overall.values()):
        overall_payload = None
    else:
        overall_payload = overall
    return fabric_list, overall_payload


@router.post("/design-preferences")
async def create_design_preferences(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
    payload: str = Form(...),
    reference_image: UploadFile | None = File(None),
):
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid JSON payload: {e}") from e

    batch_id = data.get("fabric_batch_id") or None
    if batch_id:
        b = db.query(FabricBatch).filter(FabricBatch.id == batch_id, FabricBatch.user_id == user_id).first()
        if not b:
            raise HTTPException(404, "Fabric batch not found")

    ref_path = None
    pref_id = str(uuid.uuid4())
    if reference_image and reference_image.filename:
        ref_path = await save_upload(user_id, f"design_refs/{pref_id}", reference_image)

    row = DesignPreference(
        id=pref_id,
        user_id=user_id,
        fabric_batch_id=batch_id,
        prompt_text=(data.get("prompt_text") or "").strip() or None,
        reference_image_path=ref_path,
        builtin_selections_json=json.dumps(data.get("builtin_selections") or []),
        neckline=(data.get("neckline") or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "fabric_batch_id": row.fabric_batch_id,
        "reference_image_url": public_url(ref_path) if ref_path else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/feasibility-analysis")
def run_feasibility(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
    body: dict[str, Any] = Body(...),
):
    if not body.get("design_preference_id"):
        raise HTTPException(400, "design_preference_id required")
    pref_id = body["design_preference_id"]
    pref = (
        db.query(DesignPreference)
        .filter(DesignPreference.id == pref_id, DesignPreference.user_id == user_id)
        .first()
    )
    if not pref:
        raise HTTPException(404, "Design preference not found")

    try:
        builtins = json.loads(pref.builtin_selections_json or "[]")
    except json.JSONDecodeError:
        builtins = []

    fabric_pieces, overall = _load_fabric_context(db, user_id, pref.fabric_batch_id)

    design = {
        "prompt_text": pref.prompt_text,
        "builtin_labels": builtins,
        "neckline": pref.neckline,
        "has_reference_image": bool(pref.reference_image_path),
    }

    feas = run_feasibility_analysis(
        fabric_pieces=fabric_pieces,
        fabric_batch_overall=overall,
        design=design,
    )

    fid = str(uuid.uuid4())
    fv = feas.get("feasible")
    feasible_flag = 1 if fv is True or fv == "true" or fv == 1 else 0
    rec = FeasibilityRecord(
        id=fid,
        user_id=user_id,
        design_preference_id=pref_id,
        feasible=feasible_flag,
        result_json=json.dumps(feas)[:65000],
        tailoring_plan_json=json.dumps(feas.get("tailoring_plan_steps") or [])[:16000]
        if isinstance(feas.get("tailoring_plan_steps"), list)
        else None,
        gemini_raw=json.dumps(feas)[:65000],
        gemini_ok=1 if feas.get("ok") else 0,
    )
    db.add(rec)
    db.flush()

    gen_payload = None
    gen_row = None
    if feasible_flag:
        gen = run_design_generation_spec(
            feasibility_result=feas,
            fabric_pieces=fabric_pieces,
            design=design,
        )
        gen_payload = gen
        gid = str(uuid.uuid4())
        gen_row = GeneratedDesignRecord(
            id=gid,
            feasibility_id=fid,
            specification_json=json.dumps(gen)[:65000] if gen.get("ok") else None,
            visual_description=(gen.get("customer_facing_summary") or gen.get("garment_title")) if gen.get("ok") else None,
            color_palette_json=json.dumps(gen.get("primary_colors") or []) if gen.get("ok") else None,
            gemini_raw=json.dumps(gen)[:65000],
        )
        db.add(gen_row)

    db.commit()
    db.refresh(rec)
    if gen_row:
        db.refresh(gen_row)

    return {
        "feasibility_id": fid,
        "design_preference_id": pref_id,
        "feasibility": feas,
        "generated_design": gen_payload,
    }


@router.get("/feasibility-result/{design_preference_id}")
def get_feasibility_result(
    design_preference_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    pref = (
        db.query(DesignPreference)
        .filter(DesignPreference.id == design_preference_id, DesignPreference.user_id == user_id)
        .first()
    )
    if not pref:
        raise HTTPException(404, "Not found")

    rec = (
        db.query(FeasibilityRecord)
        .options(selectinload(FeasibilityRecord.generated))
        .filter(
            FeasibilityRecord.design_preference_id == design_preference_id,
            FeasibilityRecord.user_id == user_id,
        )
        .order_by(FeasibilityRecord.created_at.desc())
        .first()
    )
    if not rec:
        return {
            "design_preference": {"id": pref.id, "fabric_batch_id": pref.fabric_batch_id},
            "feasibility": None,
            "generated_design": None,
        }

    feas_data = None
    if rec.result_json:
        try:
            feas_data = json.loads(rec.result_json)
        except json.JSONDecodeError:
            feas_data = {"raw": rec.result_json}

    gen_data = None
    if rec.generated and rec.generated.specification_json:
        try:
            gen_data = json.loads(rec.generated.specification_json)
        except json.JSONDecodeError:
            gen_data = {"raw": rec.generated.specification_json}

    return {
        "design_preference": {
            "id": pref.id,
            "fabric_batch_id": pref.fabric_batch_id,
            "prompt_text": pref.prompt_text,
            "builtin_selections": json.loads(pref.builtin_selections_json or "[]"),
            "neckline": pref.neckline,
            "reference_image_url": public_url(pref.reference_image_path) if pref.reference_image_path else None,
        },
        "feasibility": {
            "id": rec.id,
            "feasible": bool(rec.feasible),
            "data": feas_data,
            "tailoring_plan": json.loads(rec.tailoring_plan_json) if rec.tailoring_plan_json else [],
        },
        "generated_design": gen_data,
    }


# ---------------------------------------------------------------------------
# Simple Yes/No feasibility check (stateless — no DB writes needed)
# ---------------------------------------------------------------------------

class _FabricPieceIn(BaseModel):
    label: str | None = None
    length: float | None = None
    width: float | None = None
    unit: str = "inches"
    notes: str | None = None


class FeasibilityCheckRequest(BaseModel):
    fabric_pieces: list[_FabricPieceIn]
    design_description: str = ""
    selected_design_ids: list[str] = []
    neckline: str | None = None


class FeasibilityCheckResponse(BaseModel):
    feasible: bool
    reason: str


@router.post("/feasibility-check", response_model=FeasibilityCheckResponse)
def quick_feasibility_check(
    body: FeasibilityCheckRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    Stateless, fast feasibility check.
    Receives fabric pieces + design intent from the frontend and returns
    a simple yes/no answer from Gemini.
    No database writes — purely a Gemini call.
    """
    builtin_descriptions = [
        DESIGN_DESCRIPTIONS[did]
        for did in body.selected_design_ids
        if did in DESIGN_DESCRIPTIONS
    ]

    result = check_feasibility_simple(
        fabric_pieces=[p.model_dump() for p in body.fabric_pieces],
        design_description=body.design_description,
        builtin_design_descriptions=builtin_descriptions,
        neckline=body.neckline,
    )

    if not result.get("ok"):
        raise HTTPException(
            status_code=503,
            detail=result.get("error", "Gemini service unavailable. Please try again."),
        )

    return FeasibilityCheckResponse(
        feasible=bool(result.get("feasible", False)),
        reason=str(result.get("reason", "")),
    )
