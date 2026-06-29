"""
Unit tests for src/enrichment/mock_llm.py.

These tests verify that the mock LLM provider is deterministic, produces
schema-valid enrichment output, and requires nothing external — no database,
no network, no ``OPENAI_API_KEY``.  They use only pytest and the project's
Pydantic schemas.
"""

from __future__ import annotations

import os

import pytest

from src.enrichment.mock_llm import MODEL_NAME, MockLLMProvider
from src.validation.enrichment_schemas import EnrichmentOutputSchema
from src.validation.input_schemas import RawLeadSchema


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _lead_dict(**overrides) -> dict:
    base = {
        "company_name": "Acme Exports Ltd",
        "contact_email": "sales@acme-exports.example",
        "product_category": "industrial machinery",
        "contact_phone": "+1-555-0100",
        "annual_revenue": 1_500_000.0,
        "target_market": "Germany",
    }
    base.update(overrides)
    return base


@pytest.fixture
def provider() -> MockLLMProvider:
    return MockLLMProvider()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_input_returns_same_output(provider):
    lead = _lead_dict()
    first = provider.enrich_lead(lead)
    second = provider.enrich_lead(lead)
    assert first.model_dump() == second.model_dump()


def test_different_provider_instances_agree(provider):
    lead = _lead_dict()
    other = MockLLMProvider()
    assert provider.enrich_lead(lead).model_dump() == other.enrich_lead(lead).model_dump()


def test_different_leads_can_produce_different_output(provider):
    a = provider.enrich_lead(_lead_dict(company_name="Acme Exports Ltd"))
    b = provider.enrich_lead(_lead_dict(company_name="Globex Trading GmbH"))
    assert a.model_dump() != b.model_dump()


def test_context_accepted_and_deterministic(provider):
    lead = _lead_dict()
    first = provider.enrich_lead(lead, context="EU regulatory context")
    second = provider.enrich_lead(lead, context="EU regulatory context")
    assert first.model_dump() == second.model_dump()


def test_context_changes_output(provider):
    lead = _lead_dict()
    without = provider.enrich_lead(lead)
    with_context = provider.enrich_lead(lead, context="EU regulatory context")
    assert without.model_dump() != with_context.model_dump()


# ---------------------------------------------------------------------------
# Schema validity
# ---------------------------------------------------------------------------

def test_output_is_enrichment_output_schema(provider):
    result = provider.enrich_lead(_lead_dict())
    assert isinstance(result, EnrichmentOutputSchema)


def test_output_revalidates_against_schema(provider):
    result = provider.enrich_lead(_lead_dict())
    # Round-trip through the schema to prove the produced data is valid.
    EnrichmentOutputSchema.model_validate(result.model_dump())


@pytest.mark.parametrize(
    "lead",
    [
        _lead_dict(),
        _lead_dict(target_market=None),
        _lead_dict(annual_revenue=None, target_market=None),
        _lead_dict(company_name="Zeta", product_category="textiles"),
        {
            "company_name": "Minimal Co",
            "contact_email": "x@minimal.example",
            "product_category": "food",
        },
    ],
)
def test_numeric_fields_within_unit_interval(provider, lead):
    result = provider.enrich_lead(lead)
    assert 0.0 <= result.market_potential <= 1.0
    assert 0.0 <= result.export_readiness <= 1.0
    assert 0.0 <= result.confidence_score <= 1.0


def test_overall_risk_within_unit_interval(provider):
    result = provider.enrich_lead(_lead_dict())
    assert 0.0 <= result.risk_assessment.overall_risk <= 1.0


def test_recommended_markets_is_list_of_strings(provider):
    result = provider.enrich_lead(_lead_dict())
    assert isinstance(result.recommended_markets, list)
    assert len(result.recommended_markets) > 0
    assert all(isinstance(m, str) for m in result.recommended_markets)


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def test_dict_input_accepted(provider):
    result = provider.enrich_lead(_lead_dict())
    assert isinstance(result, EnrichmentOutputSchema)


def test_raw_lead_schema_input_accepted(provider):
    schema = RawLeadSchema(**_lead_dict())
    result = provider.enrich_lead(schema)
    assert isinstance(result, EnrichmentOutputSchema)


def test_dict_and_schema_inputs_agree(provider):
    lead = _lead_dict()
    from_dict = provider.enrich_lead(lead)
    from_schema = provider.enrich_lead(RawLeadSchema(**lead))
    assert from_dict.model_dump() == from_schema.model_dump()


def test_target_market_appears_in_recommended_markets(provider):
    result = provider.enrich_lead(_lead_dict(target_market="Japan"))
    assert "Japan" in result.recommended_markets


def test_missing_target_market_still_recommends_markets(provider):
    result = provider.enrich_lead(_lead_dict(target_market=None))
    assert len(result.recommended_markets) > 0
    assert all(isinstance(m, str) for m in result.recommended_markets)


# ---------------------------------------------------------------------------
# No external dependencies
# ---------------------------------------------------------------------------

def test_does_not_require_openai_api_key(provider, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert "OPENAI_API_KEY" not in os.environ
    result = provider.enrich_lead(_lead_dict())
    assert isinstance(result, EnrichmentOutputSchema)


def test_model_name_exposed(provider):
    assert provider.model_name == MODEL_NAME == "mock-llm-v1"
