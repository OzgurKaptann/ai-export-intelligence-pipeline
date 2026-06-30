"""
Pydantic response schemas for the FastAPI layer (Task 20).

These models shape the JSON returned by the API. They are ORM-compatible
(``from_attributes=True``) so route handlers can return SQLAlchemy ``ScoredLead``
objects directly and FastAPI will serialise them.

Only fields that already exist on the ``scored_leads`` table are exposed — no
fields are invented here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ScoredLeadResponse(BaseModel):
    """API representation of a single scored lead.

    Mirrors the columns of :class:`src.database.models.ScoredLead`. UUIDs are
    stored as strings on the ORM model (``as_uuid=False``) and parsed into
    :class:`uuid.UUID` here; ``score`` is a numeric column coerced to ``float``.
    """

    model_config = ConfigDict(from_attributes=True)

    scored_lead_id: UUID
    validated_lead_id: UUID
    enrichment_id: UUID
    pipeline_run_id: UUID
    company_name: str
    product_category: str
    score: float
    score_breakdown: dict
    scored_at: datetime


class PipelineRunResponse(BaseModel):
    """API representation of a single pipeline run (Task 21).

    Mirrors the columns of :class:`src.database.models.PipelineRun`. Only fields
    that actually exist on the ``pipeline_runs`` table are exposed — the
    per-stage record counts live on ``data_quality_reports`` (see
    :class:`DataQualityReportResponse`), not here.
    """

    model_config = ConfigDict(from_attributes=True)

    pipeline_run_id: UUID
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    processed_count: int
    success_count: int
    failed_count: int
    file_path: str
    run_metadata: Optional[dict] = None


class DataQualityReportResponse(BaseModel):
    """API representation of a run's data quality report (Task 21).

    Mirrors the columns of :class:`src.database.models.DataQualityReport`. The
    timestamp column on the table is ``created_at``; no fields are invented.
    """

    model_config = ConfigDict(from_attributes=True)

    report_id: UUID
    pipeline_run_id: UUID
    total_records: int
    valid_records: int
    invalid_records: int
    enriched_records: int
    failed_enrichments: int
    scored_records: int
    created_at: datetime
