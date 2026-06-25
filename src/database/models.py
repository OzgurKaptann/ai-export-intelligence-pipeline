"""
SQLAlchemy 2.0 ORM models for the AI Export Intelligence Pipeline.

Column definitions mirror migrations/001_initial_schema.sql exactly.
PostgreSQL-specific types (UUID, JSONB, ARRAY) are declared here; they will
be silently ignored by SQLite when running structural unit tests.
"""

from __future__ import annotations

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


# ---------------------------------------------------------------------------
# 1. PipelineRun
# ---------------------------------------------------------------------------

class PipelineRun(Base):
    """Tracks each end-to-end pipeline execution."""

    __tablename__ = "pipeline_runs"

    pipeline_run_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    started_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    finished_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'in_progress'"),
    )
    processed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    run_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    raw_leads: Mapped[list["RawLead"]] = relationship(
        "RawLead", back_populates="pipeline_run"
    )
    data_quality_reports: Mapped[list["DataQualityReport"]] = relationship(
        "DataQualityReport", back_populates="pipeline_run"
    )
    validation_error_records: Mapped[list["ValidationErrorRecord"]] = relationship(
        "ValidationErrorRecord", back_populates="pipeline_run"
    )


# ---------------------------------------------------------------------------
# 2. RawLead
# ---------------------------------------------------------------------------

class RawLead(Base):
    """Stores unique ingested lead records after idempotency resolution."""

    __tablename__ = "raw_leads"

    raw_lead_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    pipeline_run_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("pipeline_runs.pipeline_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_email: Mapped[str] = mapped_column(Text, nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_category: Mapped[str] = mapped_column(Text, nullable=False)
    annual_revenue: Mapped[float | None] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    target_market: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_csv_row: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ingested_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    pipeline_run: Mapped["PipelineRun"] = relationship(
        "PipelineRun", back_populates="raw_leads"
    )
    validated_leads: Mapped[list["ValidatedLead"]] = relationship(
        "ValidatedLead", back_populates="raw_lead"
    )
    validation_error_records: Mapped[list["ValidationErrorRecord"]] = relationship(
        "ValidationErrorRecord", back_populates="raw_lead"
    )


# ---------------------------------------------------------------------------
# 3. ValidatedLead
# ---------------------------------------------------------------------------

class ValidatedLead(Base):
    """Stores leads that passed RawLeadSchema Pydantic validation."""

    __tablename__ = "validated_leads"

    validated_lead_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    raw_lead_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("raw_leads.raw_lead_id", ondelete="CASCADE"),
        nullable=False,
    )
    pipeline_run_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("pipeline_runs.pipeline_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_email: Mapped[str] = mapped_column(Text, nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_category: Mapped[str] = mapped_column(Text, nullable=False)
    annual_revenue: Mapped[float | None] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    target_market: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    raw_lead: Mapped["RawLead"] = relationship(
        "RawLead", back_populates="validated_leads"
    )
    enrichments: Mapped[list["Enrichment"]] = relationship(
        "Enrichment", back_populates="validated_lead"
    )
    scored_leads: Mapped[list["ScoredLead"]] = relationship(
        "ScoredLead", back_populates="validated_lead"
    )


# ---------------------------------------------------------------------------
# 4. Enrichment
# ---------------------------------------------------------------------------

class Enrichment(Base):
    """LLM enrichment results for every validated lead, including failure details."""

    __tablename__ = "enrichments"

    enrichment_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    validated_lead_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("validated_leads.validated_lead_id", ondelete="CASCADE"),
        nullable=False,
    )
    pipeline_run_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("pipeline_runs.pipeline_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    enrichment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    market_potential: Mapped[float | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    export_readiness: Mapped[float | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    risk_assessment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    recommended_markets: Mapped[list | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    confidence_score: Mapped[float | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    error_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("0"),
    )
    raw_llm_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    enrichment_created_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    validated_lead: Mapped["ValidatedLead"] = relationship(
        "ValidatedLead", back_populates="enrichments"
    )
    scored_leads: Mapped[list["ScoredLead"]] = relationship(
        "ScoredLead", back_populates="enrichment"
    )


# ---------------------------------------------------------------------------
# 5. ScoredLead
# ---------------------------------------------------------------------------

class ScoredLead(Base):
    """Final table combining validated lead + enrichment with calculated score."""

    __tablename__ = "scored_leads"

    scored_lead_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    validated_lead_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("validated_leads.validated_lead_id", ondelete="CASCADE"),
        nullable=False,
    )
    enrichment_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("enrichments.enrichment_id", ondelete="CASCADE"),
        nullable=False,
    )
    pipeline_run_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("pipeline_runs.pipeline_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    product_category: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scored_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    validated_lead: Mapped["ValidatedLead"] = relationship(
        "ValidatedLead", back_populates="scored_leads"
    )
    enrichment: Mapped["Enrichment"] = relationship(
        "Enrichment", back_populates="scored_leads"
    )


# ---------------------------------------------------------------------------
# 6. DataQualityReport
# ---------------------------------------------------------------------------

class DataQualityReport(Base):
    """One report per pipeline run, storing counts of every stage outcome."""

    __tablename__ = "data_quality_reports"

    report_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    pipeline_run_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("pipeline_runs.pipeline_run_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    total_records: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_records: Mapped[int] = mapped_column(Integer, nullable=False)
    invalid_records: Mapped[int] = mapped_column(Integer, nullable=False)
    enriched_records: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_enrichments: Mapped[int] = mapped_column(Integer, nullable=False)
    scored_records: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    pipeline_run: Mapped["PipelineRun"] = relationship(
        "PipelineRun", back_populates="data_quality_reports"
    )


# ---------------------------------------------------------------------------
# 7. ValidationErrorRecord  (table name: validation_errors)
# ---------------------------------------------------------------------------

class ValidationErrorRecord(Base):
    """Per-field validation failures recorded during ingestion and enrichment.

    Named ``ValidationErrorRecord`` to avoid shadowing Python's built-in
    ``ValidationError``.
    """

    __tablename__ = "validation_errors"

    error_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    pipeline_run_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("pipeline_runs.pipeline_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    raw_lead_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("raw_leads.raw_lead_id", ondelete="SET NULL"),
        nullable=True,
    )
    error_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    error_field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    pipeline_run: Mapped["PipelineRun"] = relationship(
        "PipelineRun", back_populates="validation_error_records"
    )
    raw_lead: Mapped["RawLead | None"] = relationship(
        "RawLead", back_populates="validation_error_records"
    )
