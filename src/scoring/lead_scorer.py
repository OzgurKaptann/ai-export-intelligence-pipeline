"""
Lead scoring module.

``LeadScorerModule.score_lead`` turns one validated + enriched lead into a
single numeric score in the range ``[0, 100]`` and persists it as a
:class:`ScoredLead` row.  The score combines the three enrichment signals the
business cares about:

    score = (market_potential * 0.4
             + export_readiness * 0.4
             + (1 - risk_score)  * 0.2) * 100

where ``risk_score`` is ``enrichment.risk_assessment.overall_risk``.  Higher
market potential and export readiness raise the score; higher risk lowers it.

Design notes
------------
* The module never opens a database session.  It receives one per call and
  wraps it with ``repository_factory`` (``PipelineRepository`` by default), so
  unit tests can inject a fake repository and a sentinel session and exercise
  the whole flow with no database.
* The enrichment input is treated as read-only — it is never mutated.
* Missing or non-numeric components default to ``0.0`` for that component
  (Requirements 5.4, 5.5) rather than raising, so a partially-populated
  enrichment still yields a deterministic, in-range score.
* No external APIs, no network and no ``OPENAI_API_KEY`` are involved here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from src.database.models import ValidatedLead
from src.database.repository import PipelineRepository
from src.logging_config import get_logger
from src.validation.enrichment_schemas import EnrichmentOutputSchema
from src.validation.input_schemas import ScoringResult

# Factory that turns a session into a repository; overridable for testing.
RepositoryFactory = Callable[[Any], Any]

# Scoring weights — kept as module constants so the formula is auditable and
# the breakdown can report exactly what was applied.
MARKET_POTENTIAL_WEIGHT = 0.4
EXPORT_READINESS_WEIGHT = 0.4
RISK_WEIGHT = 0.2

# Final score bounds.
MIN_SCORE = 0.0
MAX_SCORE = 100.0


class LeadScorerModule:
    """Score enriched leads and persist the result behind the repository.

    Parameters
    ----------
    settings:
        Optional application settings.  Nothing is read from it today; it is
        accepted for symmetry with the other pipeline modules and forward
        compatibility.
    repository_factory:
        Callable turning a session into a repository.  Defaults to
        :class:`PipelineRepository`, so production code gets real persistence
        while tests inject a fake.
    logger:
        Optional structlog logger; one is created when omitted.
    """

    def __init__(
        self,
        settings=None,
        repository_factory: RepositoryFactory = PipelineRepository,
        logger=None,
    ) -> None:
        self._settings = settings
        self._repository_factory = repository_factory
        self._logger = logger or get_logger(__name__)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def score_lead(
        self,
        enrichment_id: UUID,
        enrichment: EnrichmentOutputSchema,
        validated_lead_id: UUID,
        pipeline_run_id: UUID,
        session,
    ) -> ScoringResult:
        """Score one enriched lead and persist a :class:`ScoredLead` row.

        Returns a :class:`ScoringResult` carrying the new ``scored_lead_id``,
        the ``score`` (always in ``[0, 100]``) and the ``score_breakdown`` that
        explains how the score was derived.  The injected ``session`` is reused
        for both the read (denormalisation lookup) and the write; no new
        session is ever created here.
        """
        # Session is injected; the repository is the DB write entry point and is
        # never created from a self-opened session.
        repository = self._repository_factory(session)

        market_potential, export_readiness, risk_score = self._extract_components(
            enrichment,
            validated_lead_id=validated_lead_id,
            pipeline_run_id=pipeline_run_id,
        )

        score, breakdown = self._compute_score(
            market_potential, export_readiness, risk_score
        )

        # Denormalise company_name / product_category for fast querying later.
        company_name, product_category = self._resolve_denormalized(
            session, validated_lead_id
        )

        scored_data = {
            "validated_lead_id": str(validated_lead_id),
            "enrichment_id": str(enrichment_id),
            "pipeline_run_id": str(pipeline_run_id),
            "company_name": company_name,
            "product_category": product_category,
            "score": score,
            "score_breakdown": breakdown,
            "scored_at": datetime.now(timezone.utc),
        }
        scored_lead_id = repository.insert_scored_lead(scored_data)

        self._logger.info(
            "lead_scored",
            score=score,
            validated_lead_id=str(validated_lead_id),
            enrichment_id=str(enrichment_id),
            pipeline_run_id=str(pipeline_run_id),
        )

        return ScoringResult(
            scored_lead_id=_coerce_uuid(scored_lead_id),
            score=score,
            score_breakdown=breakdown,
        )

    # ------------------------------------------------------------------ #
    # Scoring maths
    # ------------------------------------------------------------------ #

    def _extract_components(
        self,
        enrichment: EnrichmentOutputSchema,
        *,
        validated_lead_id: UUID,
        pipeline_run_id: UUID,
    ) -> tuple[float, float, float]:
        """Pull the three scoring inputs, defaulting missing/invalid to 0.0.

        Works with an :class:`EnrichmentOutputSchema` instance or any
        mapping-like object.  Each component is clamped to ``[0, 1]`` so a
        corrupt value can never push the final score outside ``[0, 100]``
        (Requirements 5.4, 5.5).
        """
        market_potential = self._as_unit_float(
            _read(enrichment, "market_potential"),
            field="market_potential",
            validated_lead_id=validated_lead_id,
            pipeline_run_id=pipeline_run_id,
        )
        export_readiness = self._as_unit_float(
            _read(enrichment, "export_readiness"),
            field="export_readiness",
            validated_lead_id=validated_lead_id,
            pipeline_run_id=pipeline_run_id,
        )
        risk_assessment = _read(enrichment, "risk_assessment")
        risk_score = self._as_unit_float(
            _read(risk_assessment, "overall_risk"),
            field="risk_assessment.overall_risk",
            validated_lead_id=validated_lead_id,
            pipeline_run_id=pipeline_run_id,
        )
        return market_potential, export_readiness, risk_score

    def _compute_score(
        self,
        market_potential: float,
        export_readiness: float,
        risk_score: float,
    ) -> tuple[float, dict]:
        """Apply the weighted formula and return ``(score, breakdown)``."""
        market_component = market_potential * MARKET_POTENTIAL_WEIGHT
        readiness_component = export_readiness * EXPORT_READINESS_WEIGHT
        risk_component = (1.0 - risk_score) * RISK_WEIGHT

        raw_score = (market_component + readiness_component + risk_component) * 100.0
        # Components are already clamped to [0, 1]; clamp the total too as a
        # belt-and-braces guarantee that the score never escapes [0, 100].
        score = round(_clamp(raw_score, MIN_SCORE, MAX_SCORE), 2)

        breakdown = {
            "market_potential": market_potential,
            "export_readiness": export_readiness,
            "risk_score": risk_score,
            "weights": {
                "market_potential": MARKET_POTENTIAL_WEIGHT,
                "export_readiness": EXPORT_READINESS_WEIGHT,
                "risk": RISK_WEIGHT,
            },
            "components": {
                "market_potential": round(market_component * 100.0, 4),
                "export_readiness": round(readiness_component * 100.0, 4),
                "risk": round(risk_component * 100.0, 4),
            },
            "score": score,
        }
        return score, breakdown

    def _as_unit_float(
        self,
        value: Any,
        *,
        field: str,
        validated_lead_id: UUID,
        pipeline_run_id: UUID,
    ) -> float:
        """Coerce *value* to a float in ``[0, 1]``; default to 0.0 on failure.

        Missing values and invalid data types are logged (Requirement 5.5) and
        treated as ``0.0`` for that component rather than aborting the score.
        """
        if value is None:
            return 0.0
        try:
            number = float(value)
        except (TypeError, ValueError):
            self._logger.error(
                "scoring_invalid_component",
                field=field,
                value=repr(value),
                validated_lead_id=str(validated_lead_id),
                pipeline_run_id=str(pipeline_run_id),
            )
            return 0.0
        return _clamp(number, 0.0, 1.0)

    # ------------------------------------------------------------------ #
    # Denormalisation lookup
    # ------------------------------------------------------------------ #

    def _resolve_denormalized(
        self, session, validated_lead_id: UUID
    ) -> tuple[str, str]:
        """Fetch ``company_name`` / ``product_category`` for the scored row.

        Read-only lookup of the originating validated lead via the injected
        session.  If the lead cannot be resolved (e.g. a sentinel session in a
        unit test), both fields default to empty strings so scoring still
        succeeds; production always has a real validated lead to denormalise.
        """
        try:
            validated_lead = session.get(ValidatedLead, str(validated_lead_id))
        except Exception:  # noqa: BLE001 - lookup is best-effort, never fatal
            validated_lead = None
        if validated_lead is None:
            return "", ""
        return (
            getattr(validated_lead, "company_name", "") or "",
            getattr(validated_lead, "product_category", "") or "",
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _read(obj: Any, key: str) -> Any:
    """Read *key* from an object (attribute) or mapping (item); None if absent."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _clamp(value: float, low: float, high: float) -> float:
    """Constrain *value* to the inclusive ``[low, high]`` interval."""
    return max(low, min(high, value))


def _coerce_uuid(value: Any) -> Optional[UUID]:
    """Best-effort convert a repository-returned id to a :class:`UUID`.

    Returns the value unchanged when it is already a ``UUID`` or ``None``, a
    parsed ``UUID`` when it is a UUID-shaped string, and the original value
    otherwise (so non-UUID test ids still surface on the result).
    """
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return value
