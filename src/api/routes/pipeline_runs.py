"""
FastAPI pipeline runs and quality report routes (Task 21).

Read-only endpoints over the ``pipeline_runs`` and ``data_quality_reports``
tables:

* ``GET /pipeline-runs`` — list all pipeline runs, newest first
  (``SELECT * FROM pipeline_runs ORDER BY started_at DESC``).
* ``GET /pipeline-runs/{run_id}/report`` — the data quality report for a single
  run (404 if no report exists for that run).

Both the request-scoped database session and the repository are resolved through
small, overridable dependencies (:func:`get_session` and :func:`get_repository`)
so tests can substitute fakes via ``app.dependency_overrides`` — these endpoints
never touch a live database, and need no ``DATABASE_URL`` or ``OPENAI_API_KEY``.

No configuration is read and no session is created at import time; the session
is built lazily per-request by FastAPI's ``Depends``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.schemas import DataQualityReportResponse, PipelineRunResponse
from src.database.models import PipelineRun
from src.database.repository import PipelineRepository
from src.database.session import get_db

router = APIRouter(prefix="/pipeline-runs", tags=["pipeline-runs"])


def get_session(db: Session = Depends(get_db)) -> Session:
    """Provide the request-scoped SQLAlchemy session.

    Kept as a thin dependency so the list endpoint can query ``pipeline_runs``
    directly without a dedicated repository method, while tests override it with
    a fake session.
    """
    return db


def get_repository(db: Session = Depends(get_db)) -> PipelineRepository:
    """Provide a :class:`PipelineRepository` bound to the request session.

    Overridable in tests so the report endpoint can be exercised against a fake
    repository without a database.
    """
    return PipelineRepository(db)


@router.get("", response_model=list[PipelineRunResponse])
def list_pipeline_runs(
    session: Session = Depends(get_session),
) -> list[PipelineRun]:
    """List all pipeline runs, ordered by ``started_at`` descending."""
    stmt = select(PipelineRun).order_by(PipelineRun.started_at.desc())
    return list(session.scalars(stmt).all())


@router.get("/{run_id}/report", response_model=DataQualityReportResponse)
def get_run_report(
    run_id: UUID,
    repo: PipelineRepository = Depends(get_repository),
) -> DataQualityReportResponse:
    """Return the data quality report for a run, or 404 if none exists."""
    report = repo.get_quality_report(str(run_id))
    if report is None:
        raise HTTPException(
            status_code=404, detail="Data quality report not found"
        )
    return report
