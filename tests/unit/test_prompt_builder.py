"""
Unit tests for src/enrichment/prompt_builder.py.

These tests verify that the enrichment prompt builder is deterministic,
includes the lead's fields and the JSON output contract, handles optional
fields and context cleanly, and requires nothing external — no database, no
network, no ``OPENAI_API_KEY``.  They use only pytest and the project schemas.
"""

from __future__ import annotations

import os

import pytest

from src.enrichment.prompt_builder import (
    PROMPT_VERSION,
    EnrichmentPromptBuilder,
    build_enrichment_prompt,
)
from src.validation.input_schemas import RawLeadSchema


# ---------------------------------------------------------------------------
# Helpers
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


def _minimal_lead() -> dict:
    return {
        "company_name": "Minimal Co",
        "contact_email": "x@minimal.example",
        "product_category": "food",
    }


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def test_dict_input_accepted():
    prompt = build_enrichment_prompt(_lead_dict())
    assert isinstance(prompt, str)
    assert prompt


def test_raw_lead_schema_input_accepted():
    prompt = build_enrichment_prompt(RawLeadSchema(**_lead_dict()))
    assert isinstance(prompt, str)
    assert prompt


def test_dict_and_schema_inputs_agree():
    lead = _lead_dict()
    from_dict = build_enrichment_prompt(lead)
    from_schema = build_enrichment_prompt(RawLeadSchema(**lead))
    assert from_dict == from_schema


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_input_returns_same_prompt():
    lead = _lead_dict()
    assert build_enrichment_prompt(lead) == build_enrichment_prompt(lead)


def test_same_input_with_context_is_deterministic():
    lead = _lead_dict()
    ctx = "EU machinery import regulations apply."
    assert build_enrichment_prompt(lead, ctx) == build_enrichment_prompt(lead, ctx)


def test_prompt_has_no_timestamp_or_random_marker():
    # A second call must not differ; this guards against accidental use of
    # time/random in the template.
    first = build_enrichment_prompt(_lead_dict(), "ctx")
    second = build_enrichment_prompt(_lead_dict(), "ctx")
    assert first == second


# ---------------------------------------------------------------------------
# Lead fields appear
# ---------------------------------------------------------------------------

def test_prompt_includes_required_lead_fields():
    lead = _lead_dict()
    prompt = build_enrichment_prompt(lead)
    assert lead["company_name"] in prompt
    assert lead["contact_email"] in prompt
    assert lead["product_category"] in prompt


def test_prompt_includes_optional_fields_when_present():
    lead = _lead_dict(target_market="Japan", contact_phone="+81-3-0000-0000")
    prompt = build_enrichment_prompt(lead)
    assert "Japan" in prompt
    assert "+81-3-0000-0000" in prompt
    # annual_revenue rendered (value present somewhere in the prompt)
    assert "1500000" in prompt or "1500000.0" in prompt


def test_missing_optional_fields_do_not_emit_none():
    lead = _minimal_lead()
    prompt = build_enrichment_prompt(lead)
    # Optional field labels must not appear when their value is absent.
    assert "Target market" not in prompt
    assert "Annual revenue" not in prompt
    assert "Contact phone" not in prompt
    # And no literal "None" leaking from a missing value.
    assert "None" not in prompt


def test_optional_field_set_to_none_is_omitted():
    lead = _lead_dict(target_market=None, annual_revenue=None, contact_phone=None)
    prompt = build_enrichment_prompt(lead)
    assert "Target market" not in prompt
    assert "Annual revenue" not in prompt
    assert "Contact phone" not in prompt
    assert "None" not in prompt


# ---------------------------------------------------------------------------
# Context handling
# ---------------------------------------------------------------------------

def test_context_included_when_provided():
    ctx = "Germany requires CE marking for this product category."
    prompt = build_enrichment_prompt(_lead_dict(), context=ctx)
    assert ctx in prompt
    assert "Additional context" in prompt


def test_context_section_absent_when_none():
    prompt = build_enrichment_prompt(_lead_dict(), context=None)
    assert "Additional context" not in prompt


def test_blank_context_is_treated_as_absent():
    prompt = build_enrichment_prompt(_lead_dict(), context="   ")
    assert "Additional context" not in prompt


# ---------------------------------------------------------------------------
# Output contract: JSON + schema field names + ranges
# ---------------------------------------------------------------------------

def test_prompt_requests_json_output():
    prompt = build_enrichment_prompt(_lead_dict())
    assert "JSON" in prompt


def test_prompt_mentions_enrichment_output_schema():
    prompt = build_enrichment_prompt(_lead_dict())
    assert "EnrichmentOutputSchema" in prompt


def test_prompt_includes_required_json_field_names():
    prompt = build_enrichment_prompt(_lead_dict())
    for field in (
        "market_potential",
        "export_readiness",
        "recommended_markets",
        "risk_assessment",
        "confidence_score",
        "overall_risk",
    ):
        assert field in prompt


def test_prompt_includes_numeric_range_requirements():
    prompt = build_enrichment_prompt(_lead_dict())
    # Each [0, 1] constrained field must mention the 0..1 range.
    assert prompt.count("between 0 and 1") >= 4


def test_prompt_instructs_not_to_invent_facts():
    prompt = build_enrichment_prompt(_lead_dict()).lower()
    assert "do not invent" in prompt


def test_prompt_instructs_realistic_reasoning():
    prompt = build_enrichment_prompt(_lead_dict()).lower()
    assert "realistic export" in prompt


# ---------------------------------------------------------------------------
# No external dependencies
# ---------------------------------------------------------------------------

def test_does_not_require_openai_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert "OPENAI_API_KEY" not in os.environ
    prompt = build_enrichment_prompt(_lead_dict())
    assert isinstance(prompt, str)


# ---------------------------------------------------------------------------
# Class wrapper parity
# ---------------------------------------------------------------------------

def test_builder_class_matches_function():
    lead = _lead_dict()
    assert EnrichmentPromptBuilder().build(lead) == build_enrichment_prompt(lead)
    assert EnrichmentPromptBuilder.prompt_version == PROMPT_VERSION
