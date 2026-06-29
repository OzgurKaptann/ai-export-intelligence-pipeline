"""
Unit tests for ``LLMEnrichmentModule.enrich_with_retry`` (Task 14).

These tests exercise the retry orchestration loop in complete isolation:

* No PostgreSQL / SQLite — persistence goes through an in-memory fake
  repository or a stubbed ``enrich_lead``.
* No OpenAI, no network and no ``OPENAI_API_KEY`` — mock mode only.
* No real sleeping — ``sleep_func`` is injected as a recording no-op and
  ``jitter_func`` as a deterministic stub, so the exponential-backoff maths is
  asserted without any wall-clock delay.

The retryable failure statuses (``timeout``, ``network_error``,
``rate_limited``) are not produced by the mock provider path, so they are
driven by replacing ``enrich_lead`` with a deterministic sequence of results.
Non-retryable behaviour is also verified end-to-end through the real
``enrich_lead`` + mock provider.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.enrichment.llm_enrichment import LLMEnrichmentModule
from src.enrichment.mock_llm import MockLLMProvider
from src.validation.input_schemas import EnrichmentResult, RawLeadSchema


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

SESSION = object()  # opaque sentinel — must only be forwarded, never replaced

VALIDATED_LEAD_ID = UUID("22222222-2222-2222-2222-222222222222")
PIPELINE_RUN_ID = UUID("33333333-3333-3333-3333-333333333333")
IDEMPOTENCY_KEY = "a" * 64

LEAD = RawLeadSchema(
    company_name="Acme Exports Ltd",
    contact_email="contact@acme.example.com",
    product_category="Electronics",
    target_market="Germany",
)

VALID_OUTPUT_DICT = {
    "market_potential": 0.8,
    "export_readiness": 0.7,
    "risk_assessment": {"overall_risk": 0.2},
    "recommended_markets": ["Germany", "France"],
    "confidence_score": 0.75,
}


class FakeRepository:
    """In-memory spy standing in for PipelineRepository."""

    def __init__(self) -> None:
        self.enrichments: list[dict] = []

    def insert_enrichment(self, enrichment_data: dict) -> str:
        enrichment_id = str(uuid4())
        record = dict(enrichment_data)
        record["enrichment_id"] = enrichment_id
        self.enrichments.append(record)
        return enrichment_id


class StubProvider:
    """Provider whose enrich_lead returns (or raises) a preset value."""

    model_name = "stub-llm"

    def __init__(self, output=None, exc: Exception | None = None) -> None:
        self._output = output
        self._exc = exc
        self.calls = 0

    def enrich_lead(self, lead, context=None):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._output


class RecordingSleep:
    """Records every delay it is asked to sleep for, without sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class RecordingJitter:
    """Deterministic jitter stub returning a fixed value, recording its calls."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value
        self.calls: list[tuple] = []

    def __call__(self, low: float, high: float) -> float:
        self.calls.append((low, high))
        return self.value


class SequencedEnrich:
    """Stand-in for ``enrich_lead`` that returns a preset status per call.

    Records the ``retry_count`` and ``session`` of every invocation so tests
    can assert call count, retry-count threading and session reuse.  When the
    sequence runs out the last status is repeated (so "always timeout" is easy
    to express).
    """

    def __init__(self, statuses: list[str]) -> None:
        self._statuses = list(statuses)
        self.calls: list[dict] = []

    def __call__(
        self,
        *,
        validated_lead_id,
        lead,
        idempotency_key,
        pipeline_run_id,
        session,
        retry_count: int = 0,
    ) -> EnrichmentResult:
        self.calls.append({"retry_count": retry_count, "session": session})
        idx = len(self.calls) - 1
        status = (
            self._statuses[idx]
            if idx < len(self._statuses)
            else self._statuses[-1]
        )
        return EnrichmentResult(
            enrichment_status=status,
            enrichment_id=None,
            should_retry=False,
            error_message=None if status == "success" else status,
            retry_count=retry_count,
        )


def make_settings(
    *,
    mock_enabled: bool = True,
    retry_max_attempts: int = 3,
    retry_delay_seconds: float = 2.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        MOCK_LLM_ENABLED=mock_enabled,
        OPENAI_API_KEY="",
        OPENAI_MODEL="gpt-4o-mini",
        RETRY_MAX_ATTEMPTS=retry_max_attempts,
        RETRY_DELAY_SECONDS=retry_delay_seconds,
        LLM_TIMEOUT_SECONDS=30,
    )


def build_module(
    *,
    repo: FakeRepository | None = None,
    provider=None,
    max_retries: int | None = None,
    retry_delay_seconds: float | None = None,
    sleep_func=None,
    jitter_func=None,
):
    """Wire a module whose repository factory hands back the given fake repo."""
    repo = repo or FakeRepository()
    factory_calls: list = []

    def factory(session):
        factory_calls.append(session)
        return repo

    module = LLMEnrichmentModule(
        settings=make_settings(),
        provider=provider or MockLLMProvider(),
        repository_factory=factory,
        max_retries=max_retries,
        retry_delay_seconds=retry_delay_seconds,
        sleep_func=sleep_func,
        jitter_func=jitter_func,
    )
    return module, repo, factory_calls


def run_retry(module) -> EnrichmentResult:
    return module.enrich_with_retry(
        validated_lead_id=VALIDATED_LEAD_ID,
        lead=LEAD,
        idempotency_key=IDEMPOTENCY_KEY,
        pipeline_run_id=PIPELINE_RUN_ID,
        session=SESSION,
    )


# ---------------------------------------------------------------------------
# Retryable failures are retried up to the ceiling
# ---------------------------------------------------------------------------

def test_timeout_retried_until_max_retries_and_stops():
    sleep = RecordingSleep()
    module, _, _ = build_module(
        max_retries=3, retry_delay_seconds=2.0, sleep_func=sleep,
        jitter_func=RecordingJitter(0.0),
    )
    seq = SequencedEnrich(["timeout"])  # always times out
    module.enrich_lead = seq

    result = run_retry(module)

    # Three total attempts, retry_count reaches 3, then stops (no fourth call).
    assert len(seq.calls) == 3
    assert result.retry_count == 3
    assert result.enrichment_status == "timeout"
    # Sleep only happened before the two retries, never after the final stop.
    assert len(sleep.delays) == 2


def test_retry_count_ceiling_is_respected():
    module, _, _ = build_module(
        max_retries=2, retry_delay_seconds=1.0, sleep_func=RecordingSleep(),
        jitter_func=RecordingJitter(0.0),
    )
    seq = SequencedEnrich(["timeout"])
    module.enrich_lead = seq

    result = run_retry(module)

    # max_retries=2 -> two attempts total, retry_count stops at exactly 2.
    assert len(seq.calls) == 2
    assert result.retry_count == 2


def test_network_error_is_retryable():
    sleep = RecordingSleep()
    module, _, _ = build_module(
        max_retries=3, retry_delay_seconds=1.0, sleep_func=sleep,
        jitter_func=RecordingJitter(0.0),
    )
    seq = SequencedEnrich(["network_error"])
    module.enrich_lead = seq

    result = run_retry(module)

    assert len(seq.calls) == 3
    assert result.retry_count == 3
    assert result.enrichment_status == "network_error"


def test_rate_limited_is_retryable():
    module, _, _ = build_module(
        max_retries=3, retry_delay_seconds=1.0, sleep_func=RecordingSleep(),
        jitter_func=RecordingJitter(0.0),
    )
    seq = SequencedEnrich(["rate_limited"])
    module.enrich_lead = seq

    result = run_retry(module)

    assert len(seq.calls) == 3
    assert result.retry_count == 3


def test_retry_then_success_stops_immediately():
    sleep = RecordingSleep()
    module, _, _ = build_module(
        max_retries=3, retry_delay_seconds=1.0, sleep_func=sleep,
        jitter_func=RecordingJitter(0.0),
    )
    seq = SequencedEnrich(["timeout", "timeout", "success"])
    module.enrich_lead = seq

    result = run_retry(module)

    assert len(seq.calls) == 3
    assert result.enrichment_status == "success"
    assert result.should_retry is False
    # Two retries -> two sleeps; success ends the loop without a further sleep.
    assert len(sleep.delays) == 2


# ---------------------------------------------------------------------------
# Non-retryable statuses return immediately
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status",
    [
        "validation_failed",
        "invalid_json",
        "empty_response",
        "unknown_error",
        "context_retrieval_failed",
        "some_unseen_status",  # outside the taxonomy -> non-retryable
    ],
)
def test_non_retryable_status_does_not_retry(status):
    sleep = RecordingSleep()
    module, _, _ = build_module(
        max_retries=3, retry_delay_seconds=1.0, sleep_func=sleep,
        jitter_func=RecordingJitter(0.0),
    )
    seq = SequencedEnrich([status])
    module.enrich_lead = seq

    result = run_retry(module)

    assert len(seq.calls) == 1
    assert result.enrichment_status == status
    assert result.retry_count == 0
    assert sleep.delays == []


def test_success_does_not_retry():
    sleep = RecordingSleep()
    module, _, _ = build_module(
        max_retries=3, retry_delay_seconds=1.0, sleep_func=sleep,
        jitter_func=RecordingJitter(0.0),
    )
    seq = SequencedEnrich(["success"])
    module.enrich_lead = seq

    result = run_retry(module)

    assert len(seq.calls) == 1
    assert result.enrichment_status == "success"
    assert result.retry_count == 0
    assert sleep.delays == []


def test_max_retries_zero_does_not_retry_after_first_call():
    sleep = RecordingSleep()
    module, _, _ = build_module(
        max_retries=0, retry_delay_seconds=1.0, sleep_func=sleep,
        jitter_func=RecordingJitter(0.0),
    )
    seq = SequencedEnrich(["timeout"])
    module.enrich_lead = seq

    result = run_retry(module)

    # Only the initial attempt runs; no retry, no sleep.
    assert len(seq.calls) == 1
    assert result.enrichment_status == "timeout"
    assert sleep.delays == []


# ---------------------------------------------------------------------------
# Exponential backoff with jitter
# ---------------------------------------------------------------------------

def test_backoff_formula_uses_expected_values():
    sleep = RecordingSleep()
    jitter = RecordingJitter(0.0)
    module, _, _ = build_module(
        max_retries=3, retry_delay_seconds=2.0, sleep_func=sleep,
        jitter_func=jitter,
    )
    seq = SequencedEnrich(["timeout"])
    module.enrich_lead = seq

    run_retry(module)

    # delay = RETRY_DELAY_SECONDS * (2 ** retry_count) + jitter, with jitter 0.0.
    # retry_count is 1 then 2 for the two retries -> 2*2 and 2*4.
    assert sleep.delays == [4.0, 8.0]
    # Jitter is consulted once per retry, always over the [0, 1] interval.
    assert jitter.calls == [(0.0, 1.0), (0.0, 1.0)]


def test_jitter_is_added_to_backoff_delay():
    sleep = RecordingSleep()
    jitter = RecordingJitter(0.5)  # deterministic, non-zero
    module, _, _ = build_module(
        max_retries=2, retry_delay_seconds=1.0, sleep_func=sleep,
        jitter_func=jitter,
    )
    seq = SequencedEnrich(["timeout"])
    module.enrich_lead = seq

    run_retry(module)

    # One retry for max_retries=2: delay = 1.0 * 2**1 + 0.5 = 2.5.
    assert sleep.delays == [2.5]


# ---------------------------------------------------------------------------
# Session / repository boundary and return type
# ---------------------------------------------------------------------------

def test_enrich_with_retry_returns_enrichment_result():
    module, _, _ = build_module(
        max_retries=3, retry_delay_seconds=1.0, sleep_func=RecordingSleep(),
        jitter_func=RecordingJitter(0.0),
    )
    seq = SequencedEnrich(["timeout", "success"])
    module.enrich_lead = seq

    result = run_retry(module)

    assert isinstance(result, EnrichmentResult)


def test_injected_session_is_forwarded_unchanged_on_every_attempt():
    module, _, _ = build_module(
        max_retries=3, retry_delay_seconds=1.0, sleep_func=RecordingSleep(),
        jitter_func=RecordingJitter(0.0),
    )
    seq = SequencedEnrich(["timeout"])
    module.enrich_lead = seq

    run_retry(module)

    # The same session sentinel reaches every enrich_lead call; no new session
    # is ever created inside the retry loop.
    assert [call["session"] for call in seq.calls] == [SESSION, SESSION, SESSION]
    assert [call["retry_count"] for call in seq.calls] == [0, 1, 2]


# ---------------------------------------------------------------------------
# End-to-end through the real enrich_lead (mock provider, no network)
# ---------------------------------------------------------------------------

def test_real_enrich_lead_success_path_runs_once():
    repo = FakeRepository()
    module, repo, factory_calls = build_module(
        repo=repo,
        provider=StubProvider(output=dict(VALID_OUTPUT_DICT)),
        max_retries=3, retry_delay_seconds=1.0, sleep_func=RecordingSleep(),
        jitter_func=RecordingJitter(0.0),
    )

    result = run_retry(module)

    assert result.enrichment_status == "success"
    assert result.retry_count == 0
    # Exactly one attempt persisted; the session sentinel was reused.
    assert len(repo.enrichments) == 1
    assert factory_calls == [SESSION]


def test_real_enrich_lead_validation_failed_does_not_retry():
    repo = FakeRepository()
    bad = dict(VALID_OUTPUT_DICT, market_potential=5.0)  # out of range
    module, repo, _ = build_module(
        repo=repo,
        provider=StubProvider(output=bad),
        max_retries=3, retry_delay_seconds=1.0, sleep_func=RecordingSleep(),
        jitter_func=RecordingJitter(0.0),
    )

    result = run_retry(module)

    assert result.enrichment_status == "validation_failed"
    assert result.should_retry is False
    # No retry -> only one enrichment row written.
    assert len(repo.enrichments) == 1


def test_enrich_lead_remains_backward_compatible():
    repo = FakeRepository()
    module, repo, _ = build_module(
        repo=repo,
        provider=StubProvider(output=dict(VALID_OUTPUT_DICT)),
    )

    # The original signature (no retry_count) still works.
    result = module.enrich_lead(
        validated_lead_id=VALIDATED_LEAD_ID,
        lead=LEAD,
        idempotency_key=IDEMPOTENCY_KEY,
        pipeline_run_id=PIPELINE_RUN_ID,
        session=SESSION,
    )

    assert result.enrichment_status == "success"
    assert result.retry_count == 0
    assert repo.enrichments[0]["retry_count"] == 0
