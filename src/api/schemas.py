"""
Pydantic response schemas for the FastAPI layer (Task 20).

These models shape the JSON returned by the API. They are ORM-compatible
(``from_attributes=True``) so route handlers can return SQLAlchemy ``ScoredLead``
objects directly and FastAPI will serialise them.

Only fields that already exist on the ``scored_leads`` table are exposed — no
fields are invented here.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ScoredLeadResponse(BaseModel):
    """API representation of a single scored lead.

    Mirrors the columns of :class:`src.database.models.ScoredLead`. UUIDs are
    stored as strings on the ORM model (``as_uuid=False``) and parsed into
    :class:`uuid.UUID` here; ``score`` is a numeric column coerced to ``float``.
    """

    model_config = ConfigDict(from_attributes=True)

    scored_lead_id: UUID
    validated_lead_id: UUID
    enrichment_id: UUID
    pipeline_run_id: UUID
    company_name: str
    product_category: str
    score: float
    score_breakdown: dict
    scored_at: datetime
