"""
Unit tests for ``PipelineOrchestrator.run`` (Task 17).

The orchestrator is exercised in complete isolation with fake collaborators:

* No PostgreSQL / SQLite — a single fake session and fake repository stand in
  for the database; the session is created once and reused for every lead.
* No OpenAI, no network and no ``OPENAI_API_KEY`` — the enrichment and scorer
  modules are fakes that record their calls and return canned results.
* No real sleeps, no real file I/O — ingestion is a fake that returns canned
  counts and a fake list of validated leads.

The tests assert the run lifecycle (in_progress -> completed / failed), that
ingestion is called with the generated ``pipeline_run_id``, that only
successfully enriched leads are scored, that one failing lead never stops the
run, and that a single injected session is reused throughout.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.database.models import Enrichment
from src.pipeline.orchestrator import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    PipelineOrchestrator,
    PipelineRunResult,
)
from src.validation.input_schemas import (
    EnrichmentResult,
    IngestionResult,
    ScoringResult,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RUN_UUID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
FIXED_TIME = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
FILE_PATH = "data/sample/leads.csv"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def make_validated_lead(lead_id: str, company: str = "Acme") -> SimpleNamespace:
    """A minimal stand-in for a ValidatedLead ORM row."""
    return SimpleNamespace(
        validated_lead_id=lead_id,
        company_name=company,
        contact_email=f"{lead_id}@example.com",
        product_category="Electronics",
        contact_phone=None,
        annual_revenue=None,
        target_market="Germany",
    )


def make_enrichment_row() -> SimpleNamespace:
    """A persisted-enrichment stand-in that rebuilds into EnrichmentOutputSchema."""
    return SimpleNamespace(
        market_potential=0.8,
        export_readiness=0.7,
        risk_assessment={"overall_risk": 0.2},
        recommended_markets=["Germany"],
        confidence_score=0.75,
    )


class FakeSession:
    """Single reused session; ``.get`` returns a rebuildable enrichment row."""

    def __init__(self, enrichment_row=None) -> None:
        self._enrichment_row = (
            enrichment_row if enrichment_row is not None else make_enrichment_row()
        )
        self.get_calls: list = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0

    def get(self, model, primary_key):
        self.get_calls.append((model, primary_key))
        return self._enrichment_row

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def close(self) -> None:
        self.closed += 1


class FakeRepository:
    """Records pipeline-run lifecycle calls and serves fake validated leads."""

    def __init__(self, validated_leads=None) -> None:
        self._validated_leads = list(validated_leads or [])
        self.create_calls: list = []
        self.update_calls: list = []
        self.get_validated_calls: list = []

    def create_pipeline_run(
        self, pipeline_run_id, started_at, file_path, status="in_progress"
    ):
        self.create_calls.append(
            {
                "pipeline_run_id": pipeline_run_id,
                "started_at": started_at,
                "file_path": file_path,
                "status": status,
            }
        )
        return pipeline_run_id

    def update_pipeline_run(self, pipeline_run_id, update):
        self.update_calls.append((pipeline_run_id, dict(update)))

    def get_validated_leads_for_run(self, pipeline_run_id):
        self.get_validated_calls.append(pipeline_run_id)
        return list(self._validated_leads)


class FakeIngestion:
    """Callable matching ``ingest_csv_file(file_path, run_id, repository)``."""

    def __init__(self, result: IngestionResult, raises: Exception = None) -> None:
        self._result = result
        self._raises = raises
        self.calls: list = []

    def __call__(self, file_path, pipeline_run_id, repository):
        self.calls.append((file_path, pipeline_run_id, repository))
        if self._raises is not None:
            raise self._raises
        return self._result


class FakeEnrichmentModule:
    """Returns a canned EnrichmentResult keyed by validated_lead_id."""

    def __init__(self, results_by_lead: dict) -> None:
        self._results = results_by_lead
        self.calls: list = []

    def enrich_with_retry(
        self, validated_lead_id, lead, idempotency_key, pipeline_run_id, session
    ):
        self.calls.append(
            {
                "validated_lead_id": validated_lead_id,
                "lead": lead,
                "idempotency_key": idempotency_key,
                "pipeline_run_id": pipeline_run_id,
                "session": session,
            }
        )
        return self._results[validated_lead_id]


class FakeScorerModule:
    """Records score_lead calls; optionally raises for given lead ids."""

    def __init__(self, raises_for: set = None) -> None:
        self._raises_for = raises_for or set()
        self.calls: list = []

    def score_lead(
        self, enrichment_id, enrichment, validated_lead_id, pipeline_run_id, session
    ):
        self.calls.append(
            {
                "enrichment_id": enrichment_id,
                "enrichment": enrichment,
                "validated_lead_id": validated_lead_id,
                "pipeline_run_id": pipeline_run_id,
                "session": session,
            }
        )
        if validated_lead_id in self._raises_for:
            raise RuntimeError(f"scoring blew up for {validated_lead_id}")
        return ScoringResult(scored_lead_id=uuid4(), score=76.0)


def success_result(enrichment_id="enr-1") -> EnrichmentResult:
    return EnrichmentResult(
        enrichment_status="success", enrichment_id=enrichment_id
    )


def failure_result(status="validation_failed") -> EnrichmentResult:
    return EnrichmentResult(
        enrichment_status=status,
        enrichment_id="enr-fail",
        error_message="bad output",
    )


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_orchestrator(
    *,
    validated_leads=None,
    enrichment_results=None,
    ingestion_result=None,
    ingestion_raises=None,
    scorer_raises_for=None,
    session=None,
):
    """Wire an orchestrator around fakes and return everything for assertions."""
    validated_leads = validated_leads or []
    enrichment_results = enrichment_results or {}
    ingestion_result = ingestion_result or IngestionResult(
        total=len(validated_leads), inserted=len(validated_leads), failed=0
    )

    session = session or FakeSession()
    repo = FakeRepository(validated_leads=validated_leads)
    ingestion = FakeIngestion(ingestion_result, raises=ingestion_raises)
    enrichment = FakeEnrichmentModule(enrichment_results)
    scorer = FakeScorerModule(raises_for=scorer_raises_for)

    session_factory_calls = []

    def session_factory():
        session_factory_calls.append(1)
        return session

    orchestrator = PipelineOrchestrator(
        session_factory=session_factory,
        repository_factory=lambda s: repo,
        ingestion_func=ingestion,
        enrichment_module=enrichment,
        scorer_module=scorer,
        uuid_factory=lambda: RUN_UUID,
        clock=lambda: FIXED_TIME,
    )

    return SimpleNamespace(
        orchestrator=orchestrator,
        session=session,
        repo=repo,
        ingestion=ingestion,
        enrichment=enrichment,
        scorer=scorer,
        session_factory_calls=session_factory_calls,
    )


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

def test_orchestrator_can_be_instantiated():
    orchestrator = PipelineOrchestrator()
    assert isinstance(orchestrator, PipelineOrchestrator)


def test_run_returns_pipeline_run_result():
    ctx = build_orchestrator()
    result = ctx.orchestrator.run(FILE_PATH)
    assert isinstance(result, PipelineRunResult)
    assert result.pipeline_run_id == str(RUN_UUID)


# ---------------------------------------------------------------------------
# Pipeline-run lifecycle
# ---------------------------------------------------------------------------

def test_run_creates_pipeline_run_in_progress():
    ctx = build_orchestrator()

    ctx.orchestrator.run(FILE_PATH)

    assert len(ctx.repo.create_calls) == 1
    created = ctx.repo.create_calls[0]
    assert created["pipeline_run_id"] == str(RUN_UUID)
    assert created["status"] == STATUS_IN_PROGRESS
    assert created["started_at"] == FIXED_TIME
    assert created["file_path"] == FILE_PATH


def test_run_updates_status_to_completed_on_success():
    ctx = build_orchestrator()

    result = ctx.orchestrator.run(FILE_PATH)

    assert result.status == STATUS_COMPLETED
    assert len(ctx.repo.update_calls) == 1
    _, update = ctx.repo.update_calls[0]
    assert update["status"] == STATUS_COMPLETED
    assert update["finished_at"] == FIXED_TIME


def test_run_marks_failed_on_pipeline_level_exception():
    ctx = build_orchestrator(ingestion_raises=RuntimeError("cannot read CSV"))

    result = ctx.orchestrator.run(FILE_PATH)

    assert result.status == STATUS_FAILED
    assert result.error_message == "cannot read CSV"
    # The run was created before ingestion, so the failure is recorded.
    assert ctx.repo.update_calls, "expected a failed status update"
    _, update = ctx.repo.update_calls[-1]
    assert update["status"] == STATUS_FAILED
    # A pipeline-level exception is caught and surfaced, never raised.


def test_failed_run_rolls_back_and_does_not_commit_completion():
    ctx = build_orchestrator(ingestion_raises=RuntimeError("boom"))

    ctx.orchestrator.run(FILE_PATH)

    assert ctx.session.rolled_back == 1


# ---------------------------------------------------------------------------
# Ingestion wiring
# ---------------------------------------------------------------------------

def test_run_calls_ingestion_with_generated_run_id():
    ctx = build_orchestrator()

    ctx.orchestrator.run(FILE_PATH)

    assert len(ctx.ingestion.calls) == 1
    file_path, pipeline_run_id, repository = ctx.ingestion.calls[0]
    assert file_path == FILE_PATH
    assert pipeline_run_id == str(RUN_UUID)
    assert repository is ctx.repo


def test_run_copies_ingestion_counts_onto_result():
    ctx = build_orchestrator(
        ingestion_result=IngestionResult(total=10, inserted=8, failed=2)
    )

    result = ctx.orchestrator.run(FILE_PATH)

    assert result.total_records == 10
    assert result.valid_records == 8
    assert result.invalid_records == 2


# ---------------------------------------------------------------------------
# Enrichment / scoring per lead
# ---------------------------------------------------------------------------

def test_run_enriches_each_validated_lead():
    leads = [make_validated_lead("vl-1"), make_validated_lead("vl-2")]
    ctx = build_orchestrator(
        validated_leads=leads,
        enrichment_results={
            "vl-1": success_result("enr-1"),
            "vl-2": success_result("enr-2"),
        },
    )

    ctx.orchestrator.run(FILE_PATH)

    enriched_ids = [call["validated_lead_id"] for call in ctx.enrichment.calls]
    assert enriched_ids == ["vl-1", "vl-2"]


def test_run_scores_only_successfully_enriched_leads():
    leads = [
        make_validated_lead("vl-1"),
        make_validated_lead("vl-2"),
        make_validated_lead("vl-3"),
    ]
    ctx = build_orchestrator(
        validated_leads=leads,
        enrichment_results={
            "vl-1": success_result("enr-1"),
            "vl-2": failure_result(),  # not scored
            "vl-3": success_result("enr-3"),
        },
    )

    result = ctx.orchestrator.run(FILE_PATH)

    scored_ids = [call["validated_lead_id"] for call in ctx.scorer.calls]
    assert scored_ids == ["vl-1", "vl-3"]
    assert result.enriched_records == 2
    assert result.failed_enrichments == 1
    assert result.scored_records == 2


def test_non_success_enrichment_is_counted_as_failed():
    leads = [make_validated_lead("vl-1")]
    ctx = build_orchestrator(
        validated_leads=leads,
        enrichment_results={"vl-1": failure_result("timeout")},
    )

    result = ctx.orchestrator.run(FILE_PATH)

    assert result.failed_enrichments == 1
    assert result.enriched_records == 0
    assert result.scored_records == 0
    assert ctx.scorer.calls == []


def test_failed_enrichment_for_one_lead_does_not_stop_pipeline():
    leads = [
        make_validated_lead("vl-1"),
        make_validated_lead("vl-2"),
        make_validated_lead("vl-3"),
    ]
    ctx = build_orchestrator(
        validated_leads=leads,
        enrichment_results={
            "vl-1": failure_result(),
            "vl-2": success_result("enr-2"),
            "vl-3": failure_result("network_error"),
        },
    )

    result = ctx.orchestrator.run(FILE_PATH)

    # All three leads were attempted; the run still completed.
    assert len(ctx.enrichment.calls) == 3
    assert result.status == STATUS_COMPLETED
    assert result.enriched_records == 1
    assert result.failed_enrichments == 2


def test_scoring_failure_for_one_lead_does_not_stop_others():
    leads = [
        make_validated_lead("vl-1"),
        make_validated_lead("vl-2"),
        make_validated_lead("vl-3"),
    ]
    ctx = build_orchestrator(
        validated_leads=leads,
        enrichment_results={
            "vl-1": success_result("enr-1"),
            "vl-2": success_result("enr-2"),
            "vl-3": success_result("enr-3"),
        },
        scorer_raises_for={"vl-2"},
    )

    result = ctx.orchestrator.run(FILE_PATH)

    # Every lead was still scored-attempted, and the run completed.
    attempted = [call["validated_lead_id"] for call in ctx.scorer.calls]
    assert attempted == ["vl-1", "vl-2", "vl-3"]
    assert result.status == STATUS_COMPLETED
    # vl-2 raised, so only two leads counted as scored.
    assert result.scored_records == 2
    assert result.enriched_records == 3


def test_scoring_receives_rebuilt_enrichment_output():
    leads = [make_validated_lead("vl-1")]
    ctx = build_orchestrator(
        validated_leads=leads,
        enrichment_results={"vl-1": success_result("enr-1")},
    )

    ctx.orchestrator.run(FILE_PATH)

    assert len(ctx.scorer.calls) == 1
    passed = ctx.scorer.calls[0]["enrichment"]
    # The orchestrator reloaded the enrichment row and validated it.
    assert passed.market_potential == pytest.approx(0.8)
    assert passed.risk_assessment.overall_risk == pytest.approx(0.2)
    assert ctx.session.get_calls == [(Enrichment, "enr-1")]


# ---------------------------------------------------------------------------
# Session reuse / no per-lead session
# ---------------------------------------------------------------------------

def test_single_session_is_created_and_reused():
    leads = [make_validated_lead("vl-1"), make_validated_lead("vl-2")]
    ctx = build_orchestrator(
        validated_leads=leads,
        enrichment_results={
            "vl-1": success_result("enr-1"),
            "vl-2": success_result("enr-2"),
        },
    )

    ctx.orchestrator.run(FILE_PATH)

    # Exactly one session was opened for the whole run.
    assert len(ctx.session_factory_calls) == 1
    # The same session object reached every enrichment and scoring call.
    enrich_sessions = {id(call["session"]) for call in ctx.enrichment.calls}
    score_sessions = {id(call["session"]) for call in ctx.scorer.calls}
    assert enrich_sessions == {id(ctx.session)}
    assert score_sessions == {id(ctx.session)}


def test_session_is_closed_after_run():
    ctx = build_orchestrator()
    ctx.orchestrator.run(FILE_PATH)
    assert ctx.session.closed == 1


def test_successful_run_commits_session():
    ctx = build_orchestrator()
    ctx.orchestrator.run(FILE_PATH)
    assert ctx.session.committed >= 1


# ---------------------------------------------------------------------------
# No external API / no OPENAI_API_KEY
# ---------------------------------------------------------------------------

def test_run_requires_no_openai_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    leads = [make_validated_lead("vl-1")]
    ctx = build_orchestrator(
        validated_leads=leads,
        enrichment_results={"vl-1": success_result("enr-1")},
    )

    result = ctx.orchestrator.run(FILE_PATH)

    assert result.status == STATUS_COMPLETED


def test_empty_run_completes_with_zero_counts():
    ctx = build_orchestrator(
        ingestion_result=IngestionResult(total=0, inserted=0, failed=0)
    )

    result = ctx.orchestrator.run(FILE_PATH)

    assert result.status == STATUS_COMPLETED
    assert result.enriched_records == 0
    assert result.failed_enrichments == 0
    assert result.scored_records == 0
    assert ctx.enrichment.calls == []
    assert ctx.scorer.calls == []
