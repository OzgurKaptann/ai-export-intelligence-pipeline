"""
Database repository for the AI Export Intelligence Pipeline.

``PipelineRepository`` wraps all database access behind a clean interface.
It accepts an injected SQLAlchemy ``Session`` — it never creates one itself,
which makes it trivially testable and avoids global state.

All write methods follow the same pattern:
  1. Build the ORM object from the provided dict.
  2. Add it to the session.
  3. Flush (so the DB assigns any server-side defaults, e.g. timestamps).
  4. Refresh the object so callers see the persisted state.
  5. Return the primary-key UUID string.

Callers are responsible for committing or rolling back the session.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import (
    DataQualityReport,
    Enrichment,
    PipelineRun,
    RawLead,
    ScoredLead,
    ValidatedLead,
    ValidationErrorRecord,
)


class PipelineRepository:
    """All database access for the pipeline, grouped in one place."""

    def __init__(self, session: Session) -> None:
        # Session is injected — never created here.
        self._session = session

    # ------------------------------------------------------------------
    # pipeline_runs
    # ------------------------------------------------------------------

    def create_pipeline_run(
        self,
        pipeline_run_id: str,
        started_at: datetime,
        file_path: str,
        status: str = "in_progress",
    ) -> str:
        """Insert a new pipeline_run record and return its pipeline_run_id."""
        run = PipelineRun(
            pipeline_run_id=pipeline_run_id,
            started_at=started_at,
            file_path=file_path,
            status=status,
            processed_count=0,
            success_count=0,
            failed_count=0,
        )
        self._session.add(run)
        self._session.flush()
        return run.pipeline_run_id

    def update_pipeline_run(self, pipeline_run_id: str, update: dict) -> None:
        """Apply *update* dict to the given pipeline_run row."""
        run = self._session.get(PipelineRun, pipeline_run_id)
        if run is None:
            raise ValueError(f"pipeline_run not found: {pipeline_run_id}")
        for key, value in update.items():
            setattr(run, key, value)
        self._session.flush()

    def get_pipeline_run(self, pipeline_run_id: str) -> Optional[PipelineRun]:
        """Return a PipelineRun by ID, or None if not found."""
        return self._session.get(PipelineRun, pipeline_run_id)

    # ------------------------------------------------------------------
    # raw_leads
    # ------------------------------------------------------------------

    def insert_raw_lead(self, raw_lead: dict) -> str:
        """Insert a raw_lead record and return its raw_lead_id.

        Expected keys in *raw_lead*: raw_lead_id, idempotency_key,
        pipeline_run_id, company_name, contact_email, product_category,
        raw_csv_row, ingested_at.  Optional: contact_phone, annual_revenue,
        target_market.
        """
        row = RawLead(**raw_lead)
        self._session.add(row)
        self._session.flush()
        return row.raw_lead_id

    def get_raw_lead_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[RawLead]:
        """Return the RawLead whose idempotency_key matches, or None."""
        stmt = select(RawLead).where(RawLead.idempotency_key == idempotency_key)
        return self._session.scalars(stmt).first()

    # ------------------------------------------------------------------
    # validated_leads
    # ------------------------------------------------------------------

    def insert_validated_lead(self, validated_lead: dict) -> str:
        """Insert a validated_lead record and return its validated_lead_id."""
        row = ValidatedLead(**validated_lead)
        self._session.add(row)
        self._session.flush()
        return row.validated_lead_id

    def get_validated_leads_for_run(
        self, pipeline_run_id: str
    ) -> list[ValidatedLead]:
        """Return all validated leads belonging to a pipeline run."""
        stmt = select(ValidatedLead).where(
            ValidatedLead.pipeline_run_id == pipeline_run_id
        )
        return list(self._session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # enrichments
    # ------------------------------------------------------------------

    def insert_enrichment(self, enrichment_data: dict) -> str:
        """Insert an enrichment record and return its enrichment_id."""
        row = Enrichment(**enrichment_data)
        self._session.add(row)
        self._session.flush()
        return row.enrichment_id

    def update_enrichment(self, enrichment_id: str, update: dict) -> None:
        """Apply *update* dict to an existing enrichment row."""
        row = self._session.get(Enrichment, enrichment_id)
        if row is None:
            raise ValueError(f"enrichment not found: {enrichment_id}")
        for key, value in update.items():
            setattr(row, key, value)
        self._session.flush()

    # ------------------------------------------------------------------
    # scored_leads
    # ------------------------------------------------------------------

    def insert_scored_lead(self, scored_data: dict) -> str:
        """Insert a scored_lead record and return its scored_lead_id."""
        row = ScoredLead(**scored_data)
        self._session.add(row)
        self._session.flush()
        return row.scored_lead_id

    def get_scored_leads(
        self, min_score: Optional[float] = None
    ) -> list[ScoredLead]:
        """Return all scored leads, optionally filtered by minimum score."""
        stmt = select(ScoredLead).order_by(ScoredLead.score.desc())
        if min_score is not None:
            stmt = stmt.where(ScoredLead.score >= min_score)
        return list(self._session.scalars(stmt).all())

    def get_scored_lead_by_id(
        self, scored_lead_id: str
    ) -> Optional[ScoredLead]:
        """Return a ScoredLead by primary key, or None if not found."""
        return self._session.get(ScoredLead, scored_lead_id)

    # ------------------------------------------------------------------
    # data_quality_reports
    # ------------------------------------------------------------------

    def insert_quality_report(self, report: dict) -> str:
        """Insert a data_quality_report record and return its report_id."""
        row = DataQualityReport(**report)
        self._session.add(row)
        self._session.flush()
        return row.report_id

    def get_quality_report(
        self, pipeline_run_id: str
    ) -> Optional[DataQualityReport]:
        """Return the DataQualityReport for a given pipeline run, or None."""
        stmt = select(DataQualityReport).where(
            DataQualityReport.pipeline_run_id == pipeline_run_id
        )
        return self._session.scalars(stmt).first()

    # ------------------------------------------------------------------
    # validation_errors
    # ------------------------------------------------------------------

    def insert_validation_error(self, error: dict) -> str:
        """Insert a validation_error record and return its error_id."""
        row = ValidationErrorRecord(**error)
        self._session.add(row)
        self._session.flush()
        return row.error_id
