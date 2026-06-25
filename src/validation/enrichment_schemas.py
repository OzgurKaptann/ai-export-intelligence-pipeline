"""
Enrichment output validation schemas.

All LLM responses — whether from the real API or the mock provider — are
parsed and validated against EnrichmentOutputSchema before any database
write.  This is the hard gate that keeps untrusted LLM output out of the
pipeline's storage layer.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator


class RiskAssessmentSchema(BaseModel):
    """
    Nested schema for the risk_assessment block inside EnrichmentOutputSchema.

    overall_risk is the primary signal used by the scoring formula.
    Additional keys are allowed so the LLM can include richer context
    (e.g. regulatory_risk, market_volatility) without breaking validation.
    """

    overall_risk: Annotated[float, Field(ge=0.0, le=1.0)]

    model_config = {"extra": "allow"}  # permit additional LLM-supplied keys


class EnrichmentOutputSchema(BaseModel):
    """
    Validates structured JSON produced by the LLM enrichment step.

    Every field that the scoring formula depends on is range-checked here
    so the scorer can safely assume values are in [0, 1].
    """

    market_potential: Annotated[float, Field(ge=0.0, le=1.0)]
    export_readiness: Annotated[float, Field(ge=0.0, le=1.0)]
    risk_assessment: RiskAssessmentSchema
    recommended_markets: list[str]
    confidence_score: Annotated[float, Field(ge=0.0, le=1.0)]

    @field_validator("recommended_markets", mode="before")
    @classmethod
    def _must_be_list_of_strings(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("recommended_markets must be a list")
        result = []
        for item in v:
            if not isinstance(item, str):
                raise ValueError(
                    f"every item in recommended_markets must be a string, got {type(item).__name__}"
                )
            result.append(item)
        return result
