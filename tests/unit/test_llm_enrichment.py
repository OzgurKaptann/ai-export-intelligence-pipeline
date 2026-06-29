"""
Unit tests for src/enrichment/llm_enrichment.py.

These tests exercise the enrichment flow with an in-memory fake repository and
a sentinel session — no PostgreSQL, no SQLite, no OpenAI, no network and no
``OPENAI_API_KEY``.  They cover the mock-mode success path, the
:class:`EnrichmentOutputSchema` validation gate, the failure-status taxonomy
mapping, the monkeypatch-ready real-LLM boundary, and the guarantee that the
module never opens its own database session.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.enrichment.llm_enrichment import LLMEnrichmentModule
from src.enrichment.mock_llm import MockLLMProvider
from src.validation.enrichment_schemas import EnrichmentOutputSchema
from src.validation.input_schemas import EnrichmentResult, RawLeadSchema


# ---------------------------------------------------------------------------
# Test doubles and helpers
# ---------------------------------------------------------------------------

class FakeRepository:
    """In-memory spy standing in for PipelineRepository.

    Records every enrichment insert so tests can assert on what was persisted
    without needing a database session.
    """

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


def make_settings(
    *,
    mock_enabled: bool = True,
    openai_api_key: str = "",
    openai_model: str = "gpt-4o-mini",
    retry_max_attempts: int = 3,
) -> SimpleNamespace:
    """Build a settings stub exposing only the fields the module reads."""
    return SimpleNamespace(
        MOCK_LLM_ENABLED=mock_enabled,
        OPENAI_API_KEY=openai_api_key,
        OPENAI_MODEL=openai_model,
        RETRY_MAX_ATTEMPTS=retry_max_attempts,
        LLM_TIMEOUT_SECONDS=30,
    )


SESSION = object()  # opaque sentinel — the module must only pass it to the factory

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


def build_module(repo: FakeRepository, settings=None, provider=None):
    """Wire a module with a factory that hands back the given fake repo."""
    factory_calls: list = []

    def factory(session):
        factory_calls.append(session)
        return repo

    module = LLMEnrichmentModule(
        settings=settings or make_settings(),
        provider=provider or MockLLMProvider(),
        repository_factory=factory,
    )
    return module, factory_calls


def run(module) -> EnrichmentResult:
    return module.enrich_lead(
        validated_lead_id=VALIDATED_LEAD_ID,
        lead=LEAD,
        idempotency_key=IDEMPOTENCY_KEY,
        pipeline_run_id=PIPELINE_RUN_ID,
        session=SESSION,
    )


# ---------------------------------------------------------------------------
# Mock-mode success path
# ---------------------------------------------------------------------------

def test_mock_mode_success_returns_enrichment_result():
    repo = FakeRepository()
    module, _ = build_module(repo)

    result = run(module)

    assert isinstance(result, EnrichmentResult)
    assert result.enrichment_status == "success"
    assert result.enrichment_id is not None
    assert result.should_retry is False
    assert result.retry_count == 0


def test_mock_mode_persists_success_record_with_audit_fields():
    repo = FakeRepository()
    module, _ = build_module(repo)

    run(module)

    assert len(repo.enrichments) == 1
    record = repo.enrichments[0]
    assert record["enrichment_status"] == "success"
    assert record["validated_lead_id"] == str(VALIDATED_LEAD_ID)
    assert record["pipeline_run_id"] == str(PIPELINE_RUN_ID)
    # Audit / traceability fields.
    assert record["prompt_version"] == "v1.0"
    assert record["model_name"] == MockLLMProvider.model_name
    assert record["enrichment_created_at"] is not None
    # Enrichment payload is populated and in range.
    assert 0.0 <= record["market_potential"] <= 1.0
    assert 0.0 <= record["export_readiness"] <= 1.0
    assert 0.0 <= record["confidence_score"] <= 1.0
    assert isinstance(record["recommended_markets"], list)
    assert "overall_risk" in record["risk_assessment"]


def test_mock_provider_is_used_in_mock_mode():
    repo = FakeRepository()
    provider = StubProvider(output=EnrichmentOutputSchema.model_validate(VALID_OUTPUT_DICT))
    module, _ = build_module(repo, provider=provider)

    run(module)

    assert provider.calls == 1


def test_success_output_validates_against_enrichment_schema():
    repo = FakeRepository()
    module, _ = build_module(repo)

    run(module)

    record = repo.enrichments[0]
    # The persisted enrichment payload must satisfy the gate schema.
    reconstructed = EnrichmentOutputSchema.model_validate(
        {
            "market_potential": record["market_potential"],
            "export_readiness": record["export_readiness"],
            "risk_assessment": record["risk_assessment"],
            "recommended_markets": record["recommended_markets"],
            "confidence_score": record["confidence_score"],
        }
    )
    assert isinstance(reconstructed, EnrichmentOutputSchema)


def test_openai_api_key_not_required_in_mock_mode():
    repo = FakeRepository()
    settings = make_settings(mock_enabled=True, openai_api_key="")
    module, _ = build_module(repo, settings=settings)

    result = run(module)

    assert result.enrichment_status == "success"


# ---------------------------------------------------------------------------
# Validation gate / failure taxonomy
# ---------------------------------------------------------------------------

def test_invalid_output_maps_to_validation_failed():
    repo = FakeRepository()
    # market_potential out of range -> schema validation fails.
    bad = dict(VALID_OUTPUT_DICT, market_potential=5.0)
    provider = StubProvider(output=bad)
    module, _ = build_module(repo, provider=provider)

    result = run(module)

    assert result.enrichment_status == "validation_failed"
    assert result.should_retry is False
    assert result.error_message
    record = repo.enrichments[0]
    assert record["enrichment_status"] == "validation_failed"
    assert record["error_type"] == "validation_failed"
    assert record["error_message"]
    assert record["failed_at"] is not None


def test_empty_response_maps_to_empty_response():
    repo = FakeRepository()
    provider = StubProvider(output="")
    module, _ = build_module(repo, provider=provider)

    result = run(module)

    assert result.enrichment_status == "empty_response"
    assert repo.enrichments[0]["enrichment_status"] == "empty_response"


def test_none_response_maps_to_empty_response():
    repo = FakeRepository()
    provider = StubProvider(output=None)
    module, _ = build_module(repo, provider=provider)

    result = run(module)

    assert result.enrichment_status == "empty_response"


def test_invalid_json_maps_to_invalid_json():
    repo = FakeRepository()
    provider = StubProvider(output="not-json{")
    module, _ = build_module(repo, provider=provider)

    result = run(module)

    assert result.enrichment_status == "invalid_json"
    record = repo.enrichments[0]
    assert record["enrichment_status"] == "invalid_json"
    # The unparseable payload is preserved for auditing.
    assert record["raw_llm_response"] == "not-json{"


def test_valid_json_string_is_accepted_by_gate():
    repo = FakeRepository()
    provider = StubProvider(output='{"market_potential": 0.5, "export_readiness": 0.5,'
                                    ' "risk_assessment": {"overall_risk": 0.5},'
                                    ' "recommended_markets": ["Germany"],'
                                    ' "confidence_score": 0.5}')
    module, _ = build_module(repo, provider=provider)

    result = run(module)

    assert result.enrichment_status == "success"


def test_unknown_exception_maps_to_unknown_error():
    repo = FakeRepository()
    provider = StubProvider(exc=RuntimeError("boom"))
    module, _ = build_module(repo, provider=provider)

    result = run(module)

    assert result.enrichment_status == "unknown_error"
    assert result.error_message == "boom"
    assert repo.enrichments[0]["enrichment_status"] == "unknown_error"


# ---------------------------------------------------------------------------
# Session / repository boundary
# ---------------------------------------------------------------------------

def test_no_new_session_created_when_session_provided():
    repo = FakeRepository()
    module, factory_calls = build_module(repo)

    run(module)

    # The repository factory was called exactly once, with the injected
    # session sentinel — the module never created a session of its own.
    assert factory_calls == [SESSION]


def test_repository_interaction_only_through_insert_enrichment():
    # The fake repository exposes *only* insert_enrichment; if the module
    # reached for any other method this run would raise AttributeError.
    repo = FakeRepository()
    module, _ = build_module(repo)

    result = run(module)

    assert result.enrichment_status == "success"
    assert len(repo.enrichments) == 1


# ---------------------------------------------------------------------------
# Real-LLM boundary (mockable, no network)
# ---------------------------------------------------------------------------

def test_real_llm_path_is_mockable_without_network(monkeypatch):
    repo = FakeRepository()
    settings = make_settings(mock_enabled=False)
    module, _ = build_module(repo, settings=settings)

    # Monkeypatch the isolated real-LLM seam to return a JSON string; no
    # network access, no API key.
    def fake_real(self, lead, prompt):
        return (
            '{"market_potential": 0.6, "export_readiness": 0.6,'
            ' "risk_assessment": {"overall_risk": 0.3},'
            ' "recommended_markets": ["Germany"], "confidence_score": 0.6}'
        )

    monkeypatch.setattr(LLMEnrichmentModule, "_call_real_llm", fake_real)

    result = run(module)

    assert result.enrichment_status == "success"
    assert repo.enrichments[0]["model_name"] == "gpt-4o-mini"


def test_real_llm_path_unimplemented_raises_loudly():
    repo = FakeRepository()
    settings = make_settings(mock_enabled=False)
    module, _ = build_module(repo, settings=settings)

    # Without monkeypatching, the real path is a programmer error and must not
    # be silently recorded as a runtime failure.
    with pytest.raises(NotImplementedError):
        run(module)
    assert repo.enrichments == []
