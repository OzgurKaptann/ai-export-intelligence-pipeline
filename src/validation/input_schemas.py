"""
Input validation schemas for raw lead data ingested from CSV.

All schema validation uses Pydantic v2.  These schemas are the single
source of truth for what constitutes a valid lead record; every layer
that writes to the database passes data through here first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator, model_validator


class RawLeadSchema(BaseModel):
    """
    Validates a single row parsed from the input CSV.

    Required fields: company_name, contact_email, product_category.
    Optional fields: contact_phone, annual_revenue, target_market.
    """

    company_name: str
    contact_email: EmailStr
    contact_phone: Optional[str] = None
    product_category: str
    annual_revenue: Optional[float] = None
    target_market: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Field-level validators
    # ------------------------------------------------------------------ #

    @field_validator("company_name", "product_category", mode="before")
    @classmethod
    def _reject_blank_strings(cls, v: object) -> str:
        """Required string fields must not be empty or whitespace-only."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("field must be a non-empty string")
        return v.strip()

    @field_validator("annual_revenue", mode="before")
    @classmethod
    def _reject_negative_revenue(cls, v: object) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            value = float(v)
        except (TypeError, ValueError):
            raise ValueError("annual_revenue must be a number")
        if value < 0:
            raise ValueError("annual_revenue must be non-negative")
        return value

    @field_validator("contact_phone", "target_market", mode="before")
    @classmethod
    def _coerce_empty_optional_strings(cls, v: object) -> Optional[str]:
        """Treat empty strings as None for nullable text fields."""
        if v == "" or v is None:
            return None
        return str(v).strip() or None


# ---------------------------------------------------------------------------
# Result dataclasses — plain data containers passed between pipeline modules
# ---------------------------------------------------------------------------

@dataclass
class IngestionResult:
    """Counts returned by CSVIngestionModule.ingest_file()."""

    total: int = 0
    inserted: int = 0
    skipped: int = 0         # duplicate / idempotency skip
    failed: int = 0          # schema validation failures
    pipeline_run_id: Optional[UUID] = None

    def __str__(self) -> str:
        return (
            f"IngestionResult(total={self.total}, inserted={self.inserted}, "
            f"skipped={self.skipped}, failed={self.failed})"
        )


@dataclass
class EnrichmentResult:
    """Result returned by LLMEnrichmentModule.enrich_lead()."""

    enrichment_status: str = "unknown_error"
    enrichment_id: Optional[UUID] = None
    should_retry: bool = False
    error_message: Optional[str] = None
    retry_count: int = 0


@dataclass
class ScoringResult:
    """Result returned by LeadScorerModule.score_lead()."""

    scored_lead_id: Optional[UUID] = None
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
