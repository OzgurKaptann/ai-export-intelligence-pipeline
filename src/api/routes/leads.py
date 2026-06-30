"""
FastAPI leads routes (Task 20).

Read-only endpoints over the ``scored_leads`` table:

* ``GET /leads`` — list all scored leads, optional ``min_score`` filter.
* ``GET /leads/filter`` — explicit filter endpoint (same behaviour).
* ``GET /leads/{lead_id}`` — a single scored lead by UUID (404 if missing).

The repository is resolved through :func:`get_repository`, a thin dependency
that wraps the request-scoped database session. Tests override
``get_repository`` with a fake repository, so these endpoints never touch a live
database — no ``DATABASE_URL`` and no ``OPENAI_API_KEY`` are required.

Route order matters: ``/leads/filter`` is declared **before**
``/leads/{lead_id}`` so FastAPI does not treat the literal ``filter`` as a UUID
path parameter.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.api.schemas import ScoredLeadResponse
from src.database.repository import PipelineRepository
from src.database.session import get_db

router = APIRouter(prefix="/leads", tags=["leads"])


def get_repository(db: Session = Depends(get_db)) -> PipelineRepository:
    """Provide a :class:`PipelineRepository` bound to the request session.

    Kept as a separate dependency so tests can override it with a fake
    repository via ``app.dependency_overrides`` without needing a database.
    """
    return PipelineRepository(db)


@router.get("", response_model=list[ScoredLeadResponse])
def list_leads(
    min_score: Optional[float] = None,
    repo: PipelineRepository = Depends(get_repository),
) -> list[ScoredLeadResponse]:
    """List all scored leads, optionally filtered by a minimum score."""
    return repo.get_scored_leads(min_score=min_score)


@router.get("/filter", response_model=list[ScoredLeadResponse])
def filter_leads(
    min_score: Optional[float] = None,
    repo: PipelineRepository = Depends(get_repository),
) -> list[ScoredLeadResponse]:
    """Explicit filter endpoint for scored leads (optional ``min_score``)."""
    return repo.get_scored_leads(min_score=min_score)


@router.get("/{lead_id}", response_model=ScoredLeadResponse)
def get_lead(
    lead_id: UUID,
    repo: PipelineRepository = Depends(get_repository),
) -> ScoredLeadResponse:
    """Return a single scored lead by UUID, or 404 if it does not exist."""
    lead = repo.get_scored_lead_by_id(str(lead_id))
    if lead is None:
        raise HTTPException(status_code=404, detail="Scored lead not found")
    return lead
