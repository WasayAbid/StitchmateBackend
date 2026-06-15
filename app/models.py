from datetime import datetime, timezone

from sqlalchemy import String, Text, Float, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class FabricBatch(Base):
    __tablename__ = "fabric_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    overall_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    overall_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    gemini_ok: Mapped[int] = mapped_column(Integer, default=0)
    gemini_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    gemini_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    pieces: Mapped[list["FabricPiece"]] = relationship(
        "FabricPiece", back_populates="batch", cascade="all, delete-orphan"
    )


class FabricPiece(Base):
    __tablename__ = "fabric_pieces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(36), ForeignKey("fabric_batches.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)

    image_path: Mapped[str] = mapped_column(String(512))
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    length: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    gemini_suggested_label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    gemini_piece_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    batch: Mapped["FabricBatch"] = relationship("FabricBatch", back_populates="pieces")


class DesignPreference(Base):
    __tablename__ = "design_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    fabric_batch_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("fabric_batches.id", ondelete="SET NULL"), nullable=True)
    prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    builtin_selections_json: Mapped[str] = mapped_column(Text, default="[]")
    neckline: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    feasibility_records: Mapped[list["FeasibilityRecord"]] = relationship(
        "FeasibilityRecord", back_populates="design_preference", cascade="all, delete-orphan"
    )


class FeasibilityRecord(Base):
    __tablename__ = "feasibility_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    design_preference_id: Mapped[str] = mapped_column(String(36), ForeignKey("design_preferences.id", ondelete="CASCADE"), index=True)

    feasible: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tailoring_plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    gemini_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    gemini_ok: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    design_preference: Mapped["DesignPreference"] = relationship("DesignPreference", back_populates="feasibility_records")
    generated: Mapped["GeneratedDesignRecord | None"] = relationship(
        "GeneratedDesignRecord", back_populates="feasibility", uselist=False, cascade="all, delete-orphan"
    )


class GeneratedDesignRecord(Base):
    __tablename__ = "generated_design_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    feasibility_id: Mapped[str] = mapped_column(String(36), ForeignKey("feasibility_records.id", ondelete="CASCADE"), unique=True, index=True)
    specification_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color_palette_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    gemini_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    feasibility: Mapped["FeasibilityRecord"] = relationship("FeasibilityRecord", back_populates="generated")
