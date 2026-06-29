"""
Pipeline orchestrator and pipeline_run lifecycle tracking.

``PipelineOrchestrator.run`` is the single entry point that drives one
end-to-end execution of the pipeline for a CSV file:

1. generate a ``pipeline_run_id`` and create a ``pipeline_runs`` row with
   ``status="in_progress"`` and ``started_at`` set,
2. ingest the CSV through the existing ingestion function (which writes
   ``raw_leads`` / ``validated_leads`` / ``validation_errors``),
3. for every validated lead belonging to the run, enrich it with
   :meth:`LLMEnrichmentModule.enrich_with_retry` and — only when enrichment
   succeeds — score it with :meth:`LeadScorerModule.score_lead`,
4. update the ``pipeline_runs`` row to ``completed`` (or ``failed`` on a
   pipeline-level error) with ``finished_at`` and the accumulated counts.

Design notes
------------
* The orchestrator is fully dependency-injected: a ``session_factory``,
  ``repository_factory``, ingestion function, enrichment module, scorer
  module, ``uuid_factory``, ``clock`` and ``logger`` may all be supplied.
  Production defaults are built lazily, never at import time, so importing
  this module has no side effects and reads no configuration.
* Exactly one session is opened per run and reused for ingestion, every
  enrichment and every scoring call — no session is created per lead.
* One failing lead never aborts the run: each lead is processed inside a
  try/except so the loop always continues to the next lead.  A genuine
  pipeline-level failure (e.g. the CSV cannot be read) marks the run
  ``failed`` and is surfaced on the returned result.
* No external APIs, no network and no ``OPENAI_API_KEY`` are required when the
  injected enrichment module runs in mock mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union
from uuid import UUID, uuid4

from src.database.models import Enrichment
from src.database.repository import PipelineRepository
from src.ingestion.csv_ingestion import ingest_csv_file
from src.ingestion.idempotency import generate_idempotency_key
from src.logging_config import get_logger
from src.pipeline.data_quality import generate_report as generate_data_quality_report
from src.validation.enrichment_schemas import EnrichmentOutputSchema

# Status values written to the pipeline_runs row.
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# The enrichment_status value that gates scoring.
_SUCCESS_STATUS = "success"


def _utcnow() -> datetime:
    """Timezone-aware current time; the default clock for a run."""
    return datetime.now(timezone.utc)


@dataclass
class PipelineRunResult:
    """Outcome of one :meth:`PipelineOrchestrator.run` invocation.

    Carries the run identifier, the terminal status and the per-stage counts
    so callers (and tests) can assert what happened without touching the
    database.
    """

    pipeline_run_id: str
    status: str = STATUS_IN_PROGRESS
    total_records: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    enriched_records: int = 0
    failed_enrichments: int = 0
    scored_records: int = 0
    error_message: Optional[str] = None


class PipelineOrchestrator:
    """Drive ingestion, enrichment and scoring for a single CSV run.

    Parameters
    ----------
    session_factory:
        Zero-argument callable returning a database session.  Defaults to the
        application ``SessionLocal`` factory, built lazily on first use so no
        session is created at import time.
    repository_factory:
        Callable turning a session into a repository.  Defaults to
        :class:`PipelineRepository`.
    ingestion_func:
        Callable ``(file_path, pipeline_run_id, repository) -> IngestionResult``.
        Defaults to :func:`ingest_csv_file`.
    enrichment_module:
        Object exposing ``enrich_with_retry(...)``.  Defaults to a mock-mode
        :class:`LLMEnrichmentModule` built from settings on first use.
    scorer_module:
        Object exposing ``score_lead(...)``.  Defaults to
        :class:`LeadScorerModule`.
    data_quality_func:
        Callable ``(pipeline_run_id, session) -> result`` that writes the
        ``data_quality_reports`` row after the run completes.  Defaults to
        :func:`src.pipeline.data_quality.generate_report`.  Report generation is
        best-effort: a failure here is logged and never turns an otherwise
        completed run into a failed one.
    uuid_factory:
        Zero-argument callable returning the new ``pipeline_run_id``; defaults
        to :func:`uuid.uuid4`.  Injectable for deterministic tests.
    clock:
        Zero-argument callable returning the "now" timestamp; defaults to a
        UTC clock.  Injectable for deterministic tests.
    logger:
        Optional structlog logger; one is created when omitted.
    settings:
        Optional application settings used only when default modules must be
        built.  Read lazily via ``get_settings()`` when not supplied — never at
        import time.
    """

    def __init__(
        self,
        session_factory: Optional[Callable[[], Any]] = None,
        repository_factory: Optional[Callable[[Any], Any]] = None,
        ingestion_func: Optional[Callable[..., Any]] = None,
        enrichment_module: Optional[Any] = None,
        scorer_module: Optional[Any] = None,
        data_quality_func: Optional[Callable[..., Any]] = None,
        uuid_factory: Optional[Callable[[], Any]] = None,
        clock: Optional[Callable[[], datetime]] = None,
        logger: Any = None,
        settings: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory or PipelineRepository
        self._ingestion_func = ingestion_func or ingest_csv_file
        self._enrichment_module = enrichment_module
        self._scorer_module = scorer_module
        self._data_quality_func = data_quality_func or generate_data_quality_report
        self._uuid_factory = uuid_factory or uuid4
        self._clock = clock or _utcnow
        self._logger = logger or get_logger(__name__)
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(self, file_path: Union[str, Path]) -> PipelineRunResult:
        """Execute the full pipeline for ``file_path`` and track the run.

        Returns a :class:`PipelineRunResult` with the terminal status and the
        accumulated stage counts.  A pipeline-level failure is caught, recorded
        on the ``pipeline_runs`` row (when it was created) and returned as a
        ``failed`` result rather than raised, so callers always get a result
        they can inspect.
        """
        pipeline_run_id = str(self._uuid_factory())
        result = PipelineRunResult(
            pipeline_run_id=pipeline_run_id, status=STATUS_IN_PROGRESS
        )

        session = self._open_session()
        repository = self._repository_factory(session)
        run_created = False

        try:
            repository.create_pipeline_run(
                pipeline_run_id=pipeline_run_id,
                started_at=self._clock(),
                file_path=str(file_path),
                status=STATUS_IN_PROGRESS,
            )
            run_created = True
            self._logger.info(
                "pipeline_run_started",
                pipeline_run_id=pipeline_run_id,
                file_path=str(file_path),
            )

            self._ingest(file_path, pipeline_run_id, repository, result)
            self._process_validated_leads(
                pipeline_run_id, session, repository, result
            )
            self._generate_quality_report(pipeline_run_id, session)

            result.status = STATUS_COMPLETED
            repository.update_pipeline_run(
                pipeline_run_id, self._completion_update(result)
            )
            self._commit(session)
            self._logger.info(
                "pipeline_run_completed",
                pipeline_run_id=pipeline_run_id,
                total_records=result.total_records,
                enriched_records=result.enriched_records,
                failed_enrichments=result.failed_enrichments,
                scored_records=result.scored_records,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - pipeline-level guard
            self._logger.error(
                "pipeline_run_failed",
                pipeline_run_id=pipeline_run_id,
                error=str(exc),
            )
            result.status = STATUS_FAILED
            result.error_message = str(exc)
            self._mark_failed(session, repository, pipeline_run_id, result, run_created)
            return result
        finally:
            self._close(session)

    # ------------------------------------------------------------------ #
    # Stage helpers
    # ------------------------------------------------------------------ #

    def _ingest(
        self,
        file_path: Union[str, Path],
        pipeline_run_id: str,
        repository: Any,
        result: PipelineRunResult,
    ) -> None:
        """Run CSV ingestion and copy its counts onto ``result``."""
        ingestion_result = self._ingestion_func(
            file_path, pipeline_run_id, repository
        )
        result.total_records = getattr(ingestion_result, "total", 0)
        result.valid_records = getattr(ingestion_result, "inserted", 0)
        result.invalid_records = getattr(ingestion_result, "failed", 0)

    def _process_validated_leads(
        self,
        pipeline_run_id: str,
        session: Any,
        repository: Any,
        result: PipelineRunResult,
    ) -> None:
        """Enrich and score every validated lead, isolating per-lead failures."""
        validated_leads = repository.get_validated_leads_for_run(pipeline_run_id)
        for validated_lead in validated_leads:
            try:
                self._process_one_lead(
                    validated_lead, pipeline_run_id, session, result
                )
            except Exception as exc:  # noqa: BLE001 - one lead never stops the run
                self._logger.error(
                    "pipeline_lead_failed",
                    pipeline_run_id=pipeline_run_id,
                    validated_lead_id=str(
                        getattr(validated_lead, "validated_lead_id", None)
                    ),
                    error=str(exc),
                )
                continue

    def _process_one_lead(
        self,
        validated_lead: Any,
        pipeline_run_id: str,
        session: Any,
        result: PipelineRunResult,
    ) -> None:
        """Enrich a single lead and score it when enrichment succeeds."""
        validated_lead_id = getattr(validated_lead, "validated_lead_id", None)
        lead = self._lead_to_dict(validated_lead)
        idempotency_key = generate_idempotency_key(lead)

        enrichment_result = self._enrichment().enrich_with_retry(
            validated_lead_id=validated_lead_id,
            lead=lead,
            idempotency_key=idempotency_key,
            pipeline_run_id=pipeline_run_id,
            session=session,
        )

        if getattr(enrichment_result, "enrichment_status", None) != _SUCCESS_STATUS:
            result.failed_enrichments += 1
            return

        result.enriched_records += 1

        enrichment_output = self._load_enrichment_output(
            session, getattr(enrichment_result, "enrichment_id", None)
        )
        if enrichment_output is None:
            # Enrichment succeeded but its output could not be reloaded for
            # scoring; the enrichment is still recorded, we simply skip scoring.
            self._logger.warning(
                "pipeline_scoring_skipped_no_output",
                pipeline_run_id=pipeline_run_id,
                validated_lead_id=str(validated_lead_id),
            )
            return

        self._scorer().score_lead(
            enrichment_id=getattr(enrichment_result, "enrichment_id", None),
            enrichment=enrichment_output,
            validated_lead_id=validated_lead_id,
            pipeline_run_id=pipeline_run_id,
            session=session,
        )
        result.scored_records += 1

    # ------------------------------------------------------------------ #
    # Data quality report (best-effort, after all leads are processed)
    # ------------------------------------------------------------------ #

    def _generate_quality_report(self, pipeline_run_id: str, session: Any) -> None:
        """Write the run's ``data_quality_reports`` row, reusing the session.

        Called only after ingestion, enrichment and scoring have run, so the
        report reflects the final per-stage counts.  Report generation is
        best-effort: any failure is logged and swallowed so a completed run is
        never demoted to ``failed`` because reporting alone failed.
        """
        try:
            self._data_quality_func(pipeline_run_id, session)
            self._logger.info(
                "pipeline_data_quality_report_generated",
                pipeline_run_id=pipeline_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - reporting must not fail the run
            self._logger.error(
                "pipeline_data_quality_report_failed",
                pipeline_run_id=pipeline_run_id,
                error=str(exc),
            )

    # ------------------------------------------------------------------ #
    # Enrichment output reload (smallest possible read through the session)
    # ------------------------------------------------------------------ #

    def _load_enrichment_output(
        self, session: Any, enrichment_id: Any
    ) -> Optional[EnrichmentOutputSchema]:
        """Rebuild an :class:`EnrichmentOutputSchema` from the persisted row.

        The enrichment was just written in this session, so it is read back via
        ``session.get`` and re-validated.  Any problem (missing row, session
        without ``get``, corrupt payload) yields ``None`` so the run can carry
        on without scoring this lead.
        """
        if enrichment_id is None:
            return None
        row = self._get_enrichment_row(session, enrichment_id)
        if row is None:
            return None
        try:
            return EnrichmentOutputSchema(
                market_potential=getattr(row, "market_potential", None),
                export_readiness=getattr(row, "export_readiness", None),
                risk_assessment=getattr(row, "risk_assessment", None),
                recommended_markets=getattr(row, "recommended_markets", None),
                confidence_score=getattr(row, "confidence_score", None),
            )
        except Exception:  # noqa: BLE001 - reload is best-effort, never fatal
            return None

    @staticmethod
    def _get_enrichment_row(session: Any, enrichment_id: Any) -> Optional[Any]:
        """Best-effort ``session.get(Enrichment, id)`` returning None on failure."""
        get = getattr(session, "get", None)
        if get is None:
            return None
        try:
            return get(Enrichment, str(enrichment_id))
        except Exception:  # noqa: BLE001 - lookup is best-effort
            return None

    @staticmethod
    def _lead_to_dict(validated_lead: Any) -> dict:
        """Project a validated lead onto the mapping enrichment expects."""
        fields = (
            "company_name",
            "contact_email",
            "product_category",
            "contact_phone",
            "annual_revenue",
            "target_market",
        )
        return {field: getattr(validated_lead, field, None) for field in fields}

    # ------------------------------------------------------------------ #
    # pipeline_runs update helpers
    # ------------------------------------------------------------------ #

    def _completion_update(self, result: PipelineRunResult) -> dict:
        """Build the update dict written when a run completes."""
        return {
            "status": result.status,
            "finished_at": self._clock(),
            "processed_count": result.enriched_records + result.failed_enrichments,
            "success_count": result.enriched_records,
            "failed_count": result.failed_enrichments,
        }

    def _mark_failed(
        self,
        session: Any,
        repository: Any,
        pipeline_run_id: str,
        result: PipelineRunResult,
        run_created: bool,
    ) -> None:
        """Record a pipeline-level failure on the run row, best-effort."""
        self._rollback(session)
        if not run_created:
            return
        try:
            repository.update_pipeline_run(
                pipeline_run_id,
                {
                    "status": STATUS_FAILED,
                    "finished_at": self._clock(),
                    "processed_count": result.enriched_records
                    + result.failed_enrichments,
                    "success_count": result.enriched_records,
                    "failed_count": result.failed_enrichments,
                },
            )
            self._commit(session)
        except Exception as exc:  # noqa: BLE001 - failure bookkeeping is best-effort
            self._logger.error(
                "pipeline_run_failure_not_recorded",
                pipeline_run_id=pipeline_run_id,
                error=str(exc),
            )

    # ------------------------------------------------------------------ #
    # Lazily-built default collaborators (never constructed at import time)
    # ------------------------------------------------------------------ #

    def _open_session(self) -> Any:
        """Open the single session used for the whole run."""
        if self._session_factory is not None:
            return self._session_factory()
        from src.database.session import _get_session_local

        return _get_session_local()()

    def _enrichment(self) -> Any:
        """Return the enrichment module, building a mock-mode default once."""
        if self._enrichment_module is None:
            from src.enrichment.llm_enrichment import LLMEnrichmentModule

            self._enrichment_module = LLMEnrichmentModule(self._resolve_settings())
        return self._enrichment_module

    def _scorer(self) -> Any:
        """Return the scorer module, building a default once."""
        if self._scorer_module is None:
            from src.scoring.lead_scorer import LeadScorerModule

            self._scorer_module = LeadScorerModule(self._resolve_settings())
        return self._scorer_module

    def _resolve_settings(self) -> Any:
        """Return injected settings, else load them lazily (never at import)."""
        if self._settings is None:
            from src.config import get_settings

            self._settings = get_settings()
        return self._settings

    # ------------------------------------------------------------------ #
    # Session lifecycle (guarded so fake sessions need no real methods)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _commit(session: Any) -> None:
        commit = getattr(session, "commit", None)
        if callable(commit):
            commit()

    @staticmethod
    def _rollback(session: Any) -> None:
        rollback = getattr(session, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception:  # noqa: BLE001 - rollback must never mask the cause
                pass

    @staticmethod
    def _close(session: Any) -> None:
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - close must never mask the result
                pass
