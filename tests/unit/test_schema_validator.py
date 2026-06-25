"""
Unit tests for src/validation/input_schemas.py and enrichment_schemas.py.

Covers:
  - Valid data passes
  - Missing required fields raise ValidationError with the field name
  - Invalid types fail
  - Negative annual_revenue fails
  - Out-of-range enrichment floats fail
  - risk_assessment.overall_risk outside [0, 1] fails
  - Validation error messages contain the offending field name
"""

import pytest
from pydantic import ValidationError

from src.validation.input_schemas import (
    EnrichmentResult,
    IngestionResult,
    RawLeadSchema,
    ScoringResult,
)
from src.validation.enrichment_schemas import EnrichmentOutputSchema, RiskAssessmentSchema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_LEAD = {
    "company_name": "Acme Exports Ltd",
    "contact_email": "contact@acme.example.com",
    "contact_phone": "+1-555-0100",
    "product_category": "Electronics",
    "annual_revenue": 500_000.0,
    "target_market": "Germany",
}

VALID_ENRICHMENT = {
    "market_potential": 0.75,
    "export_readiness": 0.60,
    "risk_assessment": {"overall_risk": 0.30},
    "recommended_markets": ["Germany", "France"],
    "confidence_score": 0.85,
}


def _field_names_in_error(exc_info) -> list[str]:
    """
    Return a flat list of all field-name strings found anywhere in the
    error location tuples of a ValidationError.

    Pydantic v2 reports nested errors as compound locs, e.g.
    ("risk_assessment", "overall_risk"), so we flatten all parts.
    """
    names = []
    for e in exc_info.value.errors():
        for part in e["loc"]:
            names.append(str(part))
    return names


# ===========================================================================
# RawLeadSchema — happy path
# ===========================================================================

class TestRawLeadSchemaValid:
    def test_all_fields_valid(self):
        lead = RawLeadSchema.model_validate(VALID_LEAD)
        assert lead.company_name == "Acme Exports Ltd"
        assert str(lead.contact_email) == "contact@acme.example.com"
        assert lead.product_category == "Electronics"

    def test_optional_fields_default_to_none(self):
        lead = RawLeadSchema.model_validate({
            "company_name": "Beta Co",
            "contact_email": "hello@beta.example.com",
            "product_category": "Textiles",
        })
        assert lead.contact_phone is None
        assert lead.annual_revenue is None
        assert lead.target_market is None

    def test_empty_string_optional_fields_coerced_to_none(self):
        lead = RawLeadSchema.model_validate({
            "company_name": "Gamma Inc",
            "contact_email": "g@gamma.example.com",
            "product_category": "Machinery",
            "contact_phone": "",
            "target_market": "",
        })
        assert lead.contact_phone is None
        assert lead.target_market is None

    def test_annual_revenue_zero_is_valid(self):
        lead = RawLeadSchema.model_validate({**VALID_LEAD, "annual_revenue": 0})
        assert lead.annual_revenue == 0.0

    def test_whitespace_stripped_from_required_strings(self):
        lead = RawLeadSchema.model_validate({
            **VALID_LEAD,
            "company_name": "  Trimmed Corp  ",
            "product_category": " Chemicals ",
        })
        assert lead.company_name == "Trimmed Corp"
        assert lead.product_category == "Chemicals"


# ===========================================================================
# RawLeadSchema — required field validation
# ===========================================================================

class TestRawLeadSchemaRequiredFields:
    def test_missing_company_name_raises(self):
        data = {k: v for k, v in VALID_LEAD.items() if k != "company_name"}
        with pytest.raises(ValidationError) as exc:
            RawLeadSchema.model_validate(data)
        assert "company_name" in _field_names_in_error(exc)

    def test_missing_contact_email_raises(self):
        data = {k: v for k, v in VALID_LEAD.items() if k != "contact_email"}
        with pytest.raises(ValidationError) as exc:
            RawLeadSchema.model_validate(data)
        assert "contact_email" in _field_names_in_error(exc)

    def test_missing_product_category_raises(self):
        data = {k: v for k, v in VALID_LEAD.items() if k != "product_category"}
        with pytest.raises(ValidationError) as exc:
            RawLeadSchema.model_validate(data)
        assert "product_category" in _field_names_in_error(exc)

    def test_blank_company_name_raises(self):
        with pytest.raises(ValidationError) as exc:
            RawLeadSchema.model_validate({**VALID_LEAD, "company_name": "   "})
        assert "company_name" in _field_names_in_error(exc)

    def test_blank_product_category_raises(self):
        with pytest.raises(ValidationError) as exc:
            RawLeadSchema.model_validate({**VALID_LEAD, "product_category": ""})
        assert "product_category" in _field_names_in_error(exc)


# ===========================================================================
# RawLeadSchema — email validation
# ===========================================================================

class TestRawLeadSchemaEmail:
    def test_invalid_email_raises(self):
        with pytest.raises(ValidationError) as exc:
            RawLeadSchema.model_validate({**VALID_LEAD, "contact_email": "not-an-email"})
        assert "contact_email" in _field_names_in_error(exc)

    def test_email_without_domain_raises(self):
        with pytest.raises(ValidationError):
            RawLeadSchema.model_validate({**VALID_LEAD, "contact_email": "user@"})

    def test_valid_email_with_plus_addressing(self):
        lead = RawLeadSchema.model_validate({**VALID_LEAD, "contact_email": "user+tag@example.com"})
        assert "user+tag@example.com" in str(lead.contact_email)


# ===========================================================================
# RawLeadSchema — annual_revenue validation
# ===========================================================================

class TestRawLeadSchemaAnnualRevenue:
    def test_negative_annual_revenue_raises(self):
        with pytest.raises(ValidationError) as exc:
            RawLeadSchema.model_validate({**VALID_LEAD, "annual_revenue": -1.0})
        assert "annual_revenue" in _field_names_in_error(exc)

    def test_non_numeric_annual_revenue_raises(self):
        with pytest.raises(ValidationError) as exc:
            RawLeadSchema.model_validate({**VALID_LEAD, "annual_revenue": "not-a-number"})
        assert "annual_revenue" in _field_names_in_error(exc)

    def test_annual_revenue_as_string_number_coerced(self):
        lead = RawLeadSchema.model_validate({**VALID_LEAD, "annual_revenue": "250000.50"})
        assert lead.annual_revenue == 250_000.50


# ===========================================================================
# EnrichmentOutputSchema — happy path
# ===========================================================================

class TestEnrichmentOutputSchemaValid:
    def test_all_fields_valid(self):
        enrichment = EnrichmentOutputSchema.model_validate(VALID_ENRICHMENT)
        assert enrichment.market_potential == 0.75
        assert enrichment.export_readiness == 0.60
        assert enrichment.risk_assessment.overall_risk == 0.30
        assert enrichment.recommended_markets == ["Germany", "France"]
        assert enrichment.confidence_score == 0.85

    def test_boundary_values_at_zero(self):
        data = {
            "market_potential": 0.0,
            "export_readiness": 0.0,
            "risk_assessment": {"overall_risk": 0.0},
            "recommended_markets": [],
            "confidence_score": 0.0,
        }
        enrichment = EnrichmentOutputSchema.model_validate(data)
        assert enrichment.market_potential == 0.0

    def test_boundary_values_at_one(self):
        data = {
            "market_potential": 1.0,
            "export_readiness": 1.0,
            "risk_assessment": {"overall_risk": 1.0},
            "recommended_markets": ["US"],
            "confidence_score": 1.0,
        }
        enrichment = EnrichmentOutputSchema.model_validate(data)
        assert enrichment.confidence_score == 1.0

    def test_extra_keys_in_risk_assessment_allowed(self):
        data = {
            **VALID_ENRICHMENT,
            "risk_assessment": {
                "overall_risk": 0.4,
                "regulatory_risk": 0.2,
                "market_volatility": 0.6,
            },
        }
        enrichment = EnrichmentOutputSchema.model_validate(data)
        assert enrichment.risk_assessment.overall_risk == 0.4


# ===========================================================================
# EnrichmentOutputSchema — out-of-range validation
# ===========================================================================

class TestEnrichmentOutputSchemaOutOfRange:
    @pytest.mark.parametrize("field_name, value", [
        ("market_potential", 1.1),
        ("market_potential", -0.1),
        ("export_readiness", 1.5),
        ("export_readiness", -1.0),
        ("confidence_score", 2.0),
        ("confidence_score", -0.01),
    ])
    def test_out_of_range_float_raises(self, field_name, value):
        data = {**VALID_ENRICHMENT, field_name: value}
        with pytest.raises(ValidationError) as exc:
            EnrichmentOutputSchema.model_validate(data)
        assert field_name in _field_names_in_error(exc)

    def test_overall_risk_above_one_raises(self):
        data = {
            **VALID_ENRICHMENT,
            "risk_assessment": {"overall_risk": 1.1},
        }
        with pytest.raises(ValidationError) as exc:
            EnrichmentOutputSchema.model_validate(data)
        assert "overall_risk" in _field_names_in_error(exc)

    def test_overall_risk_below_zero_raises(self):
        data = {
            **VALID_ENRICHMENT,
            "risk_assessment": {"overall_risk": -0.5},
        }
        with pytest.raises(ValidationError) as exc:
            EnrichmentOutputSchema.model_validate(data)
        assert "overall_risk" in _field_names_in_error(exc)


# ===========================================================================
# EnrichmentOutputSchema — type validation
# ===========================================================================

class TestEnrichmentOutputSchemaTypes:
    def test_missing_market_potential_raises(self):
        data = {k: v for k, v in VALID_ENRICHMENT.items() if k != "market_potential"}
        with pytest.raises(ValidationError) as exc:
            EnrichmentOutputSchema.model_validate(data)
        assert "market_potential" in _field_names_in_error(exc)

    def test_recommended_markets_not_list_raises(self):
        data = {**VALID_ENRICHMENT, "recommended_markets": "Germany"}
        with pytest.raises(ValidationError) as exc:
            EnrichmentOutputSchema.model_validate(data)
        assert "recommended_markets" in _field_names_in_error(exc)

    def test_recommended_markets_with_non_string_items_raises(self):
        data = {**VALID_ENRICHMENT, "recommended_markets": ["Germany", 42]}
        with pytest.raises(ValidationError):
            EnrichmentOutputSchema.model_validate(data)

    def test_missing_risk_assessment_raises(self):
        data = {k: v for k, v in VALID_ENRICHMENT.items() if k != "risk_assessment"}
        with pytest.raises(ValidationError) as exc:
            EnrichmentOutputSchema.model_validate(data)
        assert "risk_assessment" in _field_names_in_error(exc)


# ===========================================================================
# Result dataclasses
# ===========================================================================

class TestResultDataclasses:
    def test_ingestion_result_defaults(self):
        r = IngestionResult()
        assert r.total == 0
        assert r.inserted == 0
        assert r.skipped == 0
        assert r.failed == 0

    def test_ingestion_result_str(self):
        r = IngestionResult(total=10, inserted=8, skipped=1, failed=1)
        s = str(r)
        assert "total=10" in s
        assert "inserted=8" in s

    def test_enrichment_result_defaults(self):
        r = EnrichmentResult()
        assert r.enrichment_status == "unknown_error"
        assert r.should_retry is False
        assert r.retry_count == 0

    def test_scoring_result_defaults(self):
        r = ScoringResult()
        assert r.score == 0.0
        assert r.score_breakdown == {}
