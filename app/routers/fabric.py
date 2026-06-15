import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user_id
from app.models import FabricBatch, FabricPiece
from app.services.fabric_storage import public_url, save_upload
from app.services.gemini_client import analyze_fabric_batch

router = APIRouter(prefix="/api/fabric", tags=["fabric"])


def _piece_to_dict(p: FabricPiece) -> dict[str, Any]:
    settings = get_settings()
    url = public_url(p.image_path)
    gemini_extra = None
    if p.gemini_piece_json:
        try:
            gemini_extra = json.loads(p.gemini_piece_json)
        except json.JSONDecodeError:
            gemini_extra = {"raw": p.gemini_piece_json}
    return {
        "id": p.id,
        "batch_id": p.batch_id,
        "user_id": p.user_id,
        "image_url": url,
        "label": p.label,
        "measurements": {
            "length": p.length,
            "width": p.width,
            "unit": p.unit or "inches",
        },
        "notes": p.notes,
        "upload_timestamp": p.upload_timestamp.isoformat() if p.upload_timestamp else None,
        "gemini_suggested_label": p.gemini_suggested_label,
        "gemini_mapping": gemini_extra,
    }


class FabricPiecePatch(BaseModel):
    label: str | None = None
    length: float | None = None
    width: float | None = None
    unit: str | None = None
    notes: str | None = None


@router.post("/upload-and-analyze")
@router.post("/upload-fabric")
async def upload_and_analyze(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
    pieces_metadata: str = Form(default="[]"),
    fabric_files: list[UploadFile] = File(default_factory=list),
    overall_length: str | None = Form(None),
    overall_width: str | None = Form(None),
    overall_unit: str | None = Form(None),
    overall_notes: str | None = Form(None),
    overall_measurement_image: UploadFile | None = File(None),
):
    """
    Multipart: fabric_files (0..n), pieces_metadata JSON array aligned by index with files.
    Optional overall_* fields and overall_measurement_image.
    """
    try:
        meta_list: list[dict[str, Any]] = json.loads(pieces_metadata or "[]")
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid pieces_metadata JSON: {e}") from e

    if not fabric_files:
        raise HTTPException(400, "At least one fabric image is required")

    while len(meta_list) < len(fabric_files):
        meta_list.append({})

    meta_list = meta_list[: len(fabric_files)]

    batch_id = str(uuid.uuid4())
    batch = FabricBatch(
        id=batch_id,
        user_id=user_id,
        overall_length=float(overall_length) if overall_length not in (None, "") else None,
        overall_width=float(overall_width) if overall_width not in (None, "") else None,
        overall_unit=overall_unit or None,
        overall_notes=overall_notes or None,
    )

    settings = get_settings()
    upload_root = Path(settings.upload_dir)

    overall_rel = None
    if overall_measurement_image and overall_measurement_image.filename:
        overall_rel = await save_upload(user_id, f"batches/{batch_id}", overall_measurement_image)
        batch.overall_image_path = overall_rel

    db.add(batch)
    db.flush()

    piece_rows: list[FabricPiece] = []
    abs_paths: list[str] = []
    gemini_meta: list[dict[str, Any]] = []

    for idx, uf in enumerate(fabric_files):
        if not uf.filename:
            continue
        rel = await save_upload(user_id, f"batches/{batch_id}", uf)
        m = meta_list[idx] if idx < len(meta_list) else {}
        label = (m.get("label") or "").strip() or None
        length = m.get("length")
        width = m.get("width")
        unit = (m.get("unit") or "inches").strip() or "inches"
        notes = (m.get("notes") or "").strip() or None

        def _fnum(v: Any) -> float | None:
            if v is None or v == "":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        pid = str(uuid.uuid4())
        row = FabricPiece(
            id=pid,
            batch_id=batch_id,
            user_id=user_id,
            image_path=rel,
            label=label,
            length=_fnum(length),
            width=_fnum(width),
            unit=unit,
            notes=notes,
        )
        db.add(row)
        piece_rows.append(row)
        abs_paths.append(str(upload_root / rel))
        gemini_meta.append(
            {
                "label": label,
                "length": row.length,
                "width": row.width,
                "unit": unit,
                "notes": notes,
            }
        )

    db.commit()
    for r in piece_rows:
        db.refresh(r)

    overall_payload = None
    if batch.overall_length is not None or batch.overall_width is not None or batch.overall_notes:
        overall_payload = {
            "length": batch.overall_length,
            "width": batch.overall_width,
            "unit": batch.overall_unit,
            "notes": batch.overall_notes,
        }

    overall_abs = str(upload_root / overall_rel) if overall_rel else None

    result = analyze_fabric_batch(
        pieces_meta=gemini_meta,
        image_paths=abs_paths,
        overall=overall_payload,
        overall_image_path=overall_abs if overall_abs and Path(overall_abs).is_file() else None,
    )

    batch.gemini_ok = 1 if result.get("ok") else 0
    batch.gemini_raw = json.dumps(result)[: 65000]
    batch.gemini_error = result.get("error")

    pieces_out = result.get("pieces") or []
    for i, row in enumerate(piece_rows):
        match = next((p for p in pieces_out if p.get("index") == i), None)
        if not match and i < len(pieces_out):
            match = pieces_out[i]
        if isinstance(match, dict):
            row.gemini_suggested_label = match.get("suggested_label") or match.get("user_label")
            row.gemini_piece_json = json.dumps(match)
            if not row.label and match.get("suggested_label"):
                row.label = match.get("suggested_label")
            if row.length is None and match.get("length") is not None:
                try:
                    row.length = float(match["length"])
                except (TypeError, ValueError):
                    pass
            if row.width is None and match.get("width") is not None:
                try:
                    row.width = float(match["width"])
                except (TypeError, ValueError):
                    pass
            u = match.get("unit")
            if u and not row.unit:
                row.unit = str(u)

    db.commit()
    db.refresh(batch)
    for r in piece_rows:
        db.refresh(r)

    ordered = sorted(piece_rows, key=lambda p: (p.upload_timestamp.isoformat() if p.upload_timestamp else "", p.id))
    return {
        "ok": bool(result.get("ok")),
        "batch_id": batch_id,
        "gemini_summary": result.get("summary"),
        "gemini_error": result.get("error"),
        "overall_interpretation": result.get("overall_interpretation"),
        "pieces": [_piece_to_dict(p) for p in ordered],
        "raw_gemini": result if not result.get("ok") else None,
    }


@router.get("/batches")
def list_batches(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
    limit: int = 20,
):
    rows = (
        db.query(FabricBatch)
        .options(selectinload(FabricBatch.pieces))
        .filter(FabricBatch.user_id == user_id)
        .order_by(FabricBatch.created_at.desc())
        .limit(min(limit, 100))
        .all()
    )
    return {
        "batches": [
            {
                "id": b.id,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "gemini_ok": bool(b.gemini_ok),
                "piece_count": len(b.pieces),
            }
            for b in rows
        ]
    }


@router.get("/batches/{batch_id}")
def get_batch(
    batch_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    b = (
        db.query(FabricBatch)
        .options(selectinload(FabricBatch.pieces))
        .filter(FabricBatch.id == batch_id, FabricBatch.user_id == user_id)
        .first()
    )
    if not b:
        raise HTTPException(404, "Batch not found")
    return {
        "batch": {
            "id": b.id,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "overall": {
                "length": b.overall_length,
                "width": b.overall_width,
                "unit": b.overall_unit,
                "notes": b.overall_notes,
                "image_url": public_url(b.overall_image_path) if b.overall_image_path else None,
            },
            "gemini_ok": bool(b.gemini_ok),
            "gemini_error": b.gemini_error,
        },
        "pieces": [
            _piece_to_dict(p)
            for p in sorted(
                b.pieces,
                key=lambda x: (x.upload_timestamp.isoformat() if x.upload_timestamp else "", x.id),
            )
        ],
    }


@router.get("/user-fabrics")
def user_fabrics(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
    limit: int = 200,
):
    rows = (
        db.query(FabricPiece)
        .filter(FabricPiece.user_id == user_id)
        .order_by(FabricPiece.upload_timestamp.desc())
        .limit(min(limit, 500))
        .all()
    )
    return {"pieces": [_piece_to_dict(p) for p in rows]}


@router.patch("/pieces/{piece_id}")
def patch_fabric_piece(
    piece_id: str,
    patch: FabricPiecePatch,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    p = db.query(FabricPiece).filter(FabricPiece.id == piece_id, FabricPiece.user_id == user_id).first()
    if not p:
        raise HTTPException(404, "Piece not found")
    if patch.label is not None:
        p.label = patch.label[:256] if patch.label else None
    if patch.length is not None:
        p.length = patch.length
    if patch.width is not None:
        p.width = patch.width
    if patch.unit is not None:
        p.unit = patch.unit[:16] if patch.unit else None
    if patch.notes is not None:
        p.notes = patch.notes
    db.commit()
    db.refresh(p)
    return _piece_to_dict(p)


@router.delete("/pieces/{piece_id}")
def delete_piece(
    piece_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    p = db.query(FabricPiece).filter(FabricPiece.id == piece_id, FabricPiece.user_id == user_id).first()
    if not p:
        raise HTTPException(404, "Piece not found")
    path = Path(get_settings().upload_dir) / p.image_path
    db.delete(p)
    db.commit()
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
    return {"ok": True}
