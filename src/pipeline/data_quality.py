"""
Data quality report generation (Task 18).

``generate_report`` summarises one pipeline run into a single
``data_quality_reports`` row by counting the rows each stage produced for a
given ``pipeline_run_id``:

* ``total_records``      — rows in ``raw_leads``
* ``valid_records``      — rows in ``validated_leads``
* ``invalid_records``    — rows in ``validation_errors``
* ``enriched_records``   — ``enrichments`` with ``enrichment_status == "success"``
* ``failed_enrichments`` — ``enrichments`` with ``enrichment_status != "success"``
* ``scored_records``     — rows in ``scored_leads``

Design notes
------------
* The function never opens a database session.  It receives one per call and
  wraps it with ``repository_factory`` (:class:`PipelineRepository` by default),
  exactly like the scorer and enrichment modules, so it reuses the orchestrator's
  single per-run session and stays trivially testable.
* Counting is pure ``SELECT count(*)`` through the injected session — no rows are
  loaded into memory.
* ``report_id`` and ``created_at`` are supplied explicitly (mirroring the other
  insert paths) so the insert works identically on PostgreSQL and on the SQLite
  used by unit tests; nothing relies on a server-side default.
* Count-consistency: with the data ingestion produces, ``valid_records`` and
  ``invalid_records`` are independent counts.  For *consistent* input (test data
  where every raw lead is either validated or recorded as a validation error)
  ``valid_records + invalid_records == total_records``.  The function never
  asserts this — it reports the counts it observes and never crashes when fake
  or partial test data does not line up.
* No external APIs, no network and no ``OPENAI_API_KEY`` are involved here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select

from src.database.models import (
    Enrichment,
    RawLead,
    ScoredLead,
    ValidatedLead,
    ValidationErrorRecord,
)
from src.database.repository import PipelineRepository
from src.logging_config import get_logger

# Factory that turns a session into a repository; overridable for testing.
RepositoryFactory = Callable[[Any], Any]

# The enrichment_status value that marks a successful enrichment.
_SUCCESS_STATUS = "success"


def _utcnow() -> datetime:
    """Timezone-aware current time; the default clock for a report."""
    return datetime.now(timezone.utc)


@dataclass
class DataQualityReportResult:
    """Counts produced by :func:`generate_report` for one pipeline run.

    Carries the persisted ``report_id`` and every per-stage count so callers
    (and tests) can assert the outcome without touching the database.
    """

    report_id: Optional[str]
    pipeline_run_id: str
    total_records: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    enriched_records: int = 0
    failed_enrichments: int = 0
    scored_records: int = 0


def generate_report(
    pipeline_run_id: UUID,
    session: Any,
    *,
    repository_factory: RepositoryFactory = PipelineRepository,
    clock: Callable[[], datetime] = _utcnow,
    uuid_factory: Callable[[], Any] = uuid4,
    logger: Any = None,
) -> DataQualityReportResult:
    """Count one run's stage outcomes and persist a data-quality report.

    Parameters
    ----------
    pipeline_run_id:
        The run to summarise; accepted as a ``UUID`` or a string.
    session:
        An already-open database session.  It is reused for both the count
        queries and the insert — **no new session is ever created here**.
    repository_factory:
        Callable turning the session into a repository; defaults to
        :class:`PipelineRepository`.  The report is persisted through the
        repository's :meth:`insert_quality_report`, never with duplicated
        persistence logic.
    clock / uuid_factory:
        Injectable ``now`` and id sources for deterministic tests.
    logger:
        Optional structlog logger; one is created when omitted.

    Returns
    -------
    DataQualityReportResult
        The persisted ``report_id`` and all six counts.
    """
    log = logger or get_logger(__name__)
    run_id = str(pipeline_run_id)
    repository = repository_factory(session)

    total_records = _count(session, RawLead, run_id)
    valid_records = _count(session, ValidatedLead, run_id)
    invalid_records = _count(session, ValidationErrorRecord, run_id)
    enriched_records = _count(
        session, Enrichment, run_id, Enrichment.enrichment_status == _SUCCESS_STATUS
    )
    failed_enrichments = _count(
        session, Enrichment, run_id, Enrichment.enrichment_status != _SUCCESS_STATUS
    )
    scored_records = _count(session, ScoredLead, run_id)

    report = {
        "report_id": str(uuid_factory()),
        "pipeline_run_id": run_id,
        "total_records": total_records,
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "enriched_records": enriched_records,
        "failed_enrichments": failed_enrichments,
        "scored_records": scored_records,
        "created_at": clock(),
    }
    report_id = repository.insert_quality_report(report)

    log.info(
        "data_quality_report_generated",
        pipeline_run_id=run_id,
        total_records=total_records,
        valid_records=valid_records,
        invalid_records=invalid_records,
        enriched_records=enriched_records,
        failed_enrichments=failed_enrichments,
        scored_records=scored_records,
    )

    return DataQualityReportResult(
        report_id=report_id,
        pipeline_run_id=run_id,
        total_records=total_records,
        valid_records=valid_records,
        invalid_records=invalid_records,
        enriched_records=enriched_records,
        failed_enrichments=failed_enrichments,
        scored_records=scored_records,
    )


def _count(
    session: Any,
    model: Any,
    pipeline_run_id: str,
    extra_filter: Any = None,
) -> int:
    """Return ``SELECT count(*)`` for *model* rows belonging to the run.

    A second optional SQLAlchemy boolean expression (``extra_filter``) narrows
    the count further — used to split enrichments into success vs failure.  The
    injected session executes the statement; the result is coerced to ``int``
    and defaults to ``0`` when the session returns ``None``.
    """
    stmt = select(func.count()).select_from(model).where(
        model.pipeline_run_id == pipeline_run_id
    )
    if extra_filter is not None:
        stmt = stmt.where(extra_filter)
    return int(session.scalar(stmt) or 0)
