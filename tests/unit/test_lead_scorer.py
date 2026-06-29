"""
Unit tests for ``LeadScorerModule.score_lead`` (Task 15).

These tests exercise the scoring formula and persistence boundary in complete
isolation:

* No PostgreSQL / SQLite — persistence goes through an in-memory fake
  repository; the denormalisation read goes through a fake session.
* No OpenAI, no network and no ``OPENAI_API_KEY``.
* The enrichment input is never mutated and no new session is created
  internally.

Reference formula::

    score = (market_potential * 0.4
             + export_readiness * 0.4
             + (1 - risk_score)  * 0.2) * 100
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.database.models import ValidatedLead
from src.scoring.lead_scorer import LeadScorerModule
from src.validation.enrichment_schemas import EnrichmentOutputSchema
from src.validation.input_schemas import ScoringResult


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

SESSION = object()  # opaque sentinel — must only be forwarded, never replaced

ENRICHMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
VALIDATED_LEAD_ID = UUID("22222222-2222-2222-2222-222222222222")
PIPELINE_RUN_ID = UUID("33333333-3333-3333-3333-333333333333")


def make_enrichment(
    *,
    market_potential: float = 0.8,
    export_readiness: float = 0.7,
    overall_risk: float = 0.2,
) -> EnrichmentOutputSchema:
    return EnrichmentOutputSchema(
        market_potential=market_potential,
        export_readiness=export_readiness,
        risk_assessment={"overall_risk": overall_risk},
        recommended_markets=["Germany", "France"],
        confidence_score=0.75,
    )


def expected_score(market_potential, export_readiness, overall_risk) -> float:
    return (
        market_potential * 0.4
        + export_readiness * 0.4
        + (1 - overall_risk) * 0.2
    ) * 100


class FakeRepository:
    """In-memory spy standing in for PipelineRepository."""

    def __init__(self) -> None:
        self.scored_leads: list[dict] = []

    def insert_scored_lead(self, scored_data: dict) -> str:
        scored_lead_id = str(uuid4())
        record = dict(scored_data)
        record["scored_lead_id"] = scored_lead_id
        self.scored_leads.append(record)
        return scored_lead_id


class FakeSession:
    """Session stub whose ``.get`` returns a denormalisable validated lead."""

    def __init__(self, validated_lead=None) -> None:
        self._validated_lead = validated_lead
        self.get_calls: list = []

    def get(self, model, primary_key):
        self.get_calls.append((model, primary_key))
        return self._validated_lead


def build_module(repo: FakeRepository | None = None):
    """Wire a module whose repository factory hands back the given fake repo."""
    repo = repo or FakeRepository()
    factory_calls: list = []

    def factory(session):
        factory_calls.append(session)
        return repo

    module = LeadScorerModule(repository_factory=factory)
    return module, repo, factory_calls


def run_score(module, enrichment, session=SESSION) -> ScoringResult:
    return module.score_lead(
        enrichment_id=ENRICHMENT_ID,
        enrichment=enrichment,
        validated_lead_id=VALIDATED_LEAD_ID,
        pipeline_run_id=PIPELINE_RUN_ID,
        session=session,
    )


# ---------------------------------------------------------------------------
# Formula correctness
# ---------------------------------------------------------------------------

def test_score_formula_with_known_values():
    module, _, _ = build_module()
    enrichment = make_enrichment(
        market_potential=0.8, export_readiness=0.7, overall_risk=0.2
    )

    result = run_score(module, enrichment)

    # (0.8*0.4 + 0.7*0.4 + 0.8*0.2) * 100 = 76.0
    assert result.score == pytest.approx(76.0)
    assert result.score == pytest.approx(expected_score(0.8, 0.7, 0.2))


def test_score_is_100_when_all_signals_maximal():
    module, _, _ = build_module()
    enrichment = make_enrichment(
        market_potential=1.0, export_readiness=1.0, overall_risk=0.0
    )

    result = run_score(module, enrichment)

    assert result.score == pytest.approx(100.0)


def test_score_is_low_when_all_signals_minimal():
    module, _, _ = build_module()
    enrichment = make_enrichment(
        market_potential=0.0, export_readiness=0.0, overall_risk=1.0
    )

    result = run_score(module, enrichment)

    # (0 + 0 + (1-1)*0.2) * 100 = 0
    assert result.score == pytest.approx(0.0)


@pytest.mark.parametrize(
    "market_potential,export_readiness,overall_risk",
    [
        (0.5, 0.5, 0.5),
        (0.1, 0.9, 0.3),
        (0.33, 0.66, 0.1),
        (1.0, 0.0, 0.5),
        (0.0, 1.0, 0.0),
    ],
)
def test_score_matches_reference_formula(
    market_potential, export_readiness, overall_risk
):
    module, _, _ = build_module()
    enrichment = make_enrichment(
        market_potential=market_potential,
        export_readiness=export_readiness,
        overall_risk=overall_risk,
    )

    result = run_score(module, enrichment)

    assert result.score == pytest.approx(
        expected_score(market_potential, export_readiness, overall_risk), abs=1e-9
    )


# ---------------------------------------------------------------------------
# Monotonicity: each signal moves the score in the right direction
# ---------------------------------------------------------------------------

def test_higher_market_potential_increases_score():
    module, _, _ = build_module()
    low = run_score(module, make_enrichment(market_potential=0.2)).score
    high = run_score(module, make_enrichment(market_potential=0.9)).score
    assert high > low


def test_higher_export_readiness_increases_score():
    module, _, _ = build_module()
    low = run_score(module, make_enrichment(export_readiness=0.2)).score
    high = run_score(module, make_enrichment(export_readiness=0.9)).score
    assert high > low


def test_higher_risk_decreases_score():
    module, _, _ = build_module()
    low_risk = run_score(module, make_enrichment(overall_risk=0.1)).score
    high_risk = run_score(module, make_enrichment(overall_risk=0.9)).score
    assert high_risk < low_risk


# ---------------------------------------------------------------------------
# Score always stays within [0, 100]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "market_potential,export_readiness,overall_risk",
    [
        (0.0, 0.0, 0.0),
        (1.0, 1.0, 1.0),
        (0.0, 0.0, 1.0),
        (1.0, 1.0, 0.0),
        (0.5, 0.5, 0.5),
    ],
)
def test_score_within_bounds(market_potential, export_readiness, overall_risk):
    module, _, _ = build_module()
    enrichment = make_enrichment(
        market_potential=market_potential,
        export_readiness=export_readiness,
        overall_risk=overall_risk,
    )

    result = run_score(module, enrichment)

    assert 0.0 <= result.score <= 100.0


# ---------------------------------------------------------------------------
# Input acceptance and missing-field handling (default to 0.0)
# ---------------------------------------------------------------------------

def test_enrichment_output_schema_input_is_accepted():
    module, _, _ = build_module()
    enrichment = make_enrichment()

    result = run_score(module, enrichment)

    assert isinstance(result, ScoringResult)
    assert result.score == pytest.approx(expected_score(0.8, 0.7, 0.2))


def test_missing_components_default_to_zero():
    module, _, _ = build_module()
    # A mapping-like enrichment missing every scoring field; each component
    # must default to 0.0 -> only the (1 - 0) * 0.2 risk term contributes.
    enrichment = {"recommended_markets": []}

    result = run_score(module, enrichment)

    # (0*0.4 + 0*0.4 + (1-0)*0.2) * 100 = 20.0
    assert result.score == pytest.approx(20.0)


def test_missing_overall_risk_defaults_to_zero_risk():
    module, _, _ = build_module()
    enrichment = {
        "market_potential": 0.5,
        "export_readiness": 0.5,
        "risk_assessment": {},  # overall_risk missing -> treated as 0.0
    }

    result = run_score(module, enrichment)

    # (0.5*0.4 + 0.5*0.4 + (1-0)*0.2) * 100 = 60.0
    assert result.score == pytest.approx(60.0)


def test_invalid_component_type_defaults_to_zero():
    module, _, _ = build_module()
    enrichment = {
        "market_potential": "not-a-number",
        "export_readiness": 1.0,
        "risk_assessment": {"overall_risk": 0.0},
    }

    result = run_score(module, enrichment)

    # market_potential -> 0.0; (0 + 1*0.4 + 1*0.2) * 100 = 60.0
    assert result.score == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# Persistence boundary
# ---------------------------------------------------------------------------

def test_returns_scoring_result():
    module, _, _ = build_module()
    result = run_score(module, make_enrichment())
    assert isinstance(result, ScoringResult)
    assert result.scored_lead_id is not None
    assert isinstance(result.score_breakdown, dict)


def test_stores_via_insert_scored_lead():
    module, repo, _ = build_module()

    result = run_score(module, make_enrichment())

    assert len(repo.scored_leads) == 1
    stored = repo.scored_leads[0]
    assert stored["score"] == pytest.approx(result.score)
    assert stored["validated_lead_id"] == str(VALIDATED_LEAD_ID)
    assert stored["enrichment_id"] == str(ENRICHMENT_ID)
    assert stored["pipeline_run_id"] == str(PIPELINE_RUN_ID)
    assert "score_breakdown" in stored
    assert "scored_at" in stored


def test_no_new_session_is_created_internally():
    module, _, factory_calls = build_module()

    run_score(module, make_enrichment())

    # The exact injected session sentinel reaches the repository factory.
    assert factory_calls == [SESSION]


def test_denormalizes_company_and_product_from_validated_lead():
    repo = FakeRepository()
    module, repo, _ = build_module(repo=repo)
    validated_lead = SimpleNamespace(
        company_name="Acme Exports Ltd", product_category="Electronics"
    )
    session = FakeSession(validated_lead=validated_lead)

    run_score(module, make_enrichment(), session=session)

    stored = repo.scored_leads[0]
    assert stored["company_name"] == "Acme Exports Ltd"
    assert stored["product_category"] == "Electronics"
    # The lookup used the provided session keyed by the validated lead id.
    assert session.get_calls == [(ValidatedLead, str(VALIDATED_LEAD_ID))]


def test_denormalization_defaults_to_empty_when_lead_unavailable():
    module, repo, _ = build_module()
    # SESSION sentinel has no .get -> denormalisation falls back gracefully.
    run_score(module, make_enrichment())
    stored = repo.scored_leads[0]
    assert stored["company_name"] == ""
    assert stored["product_category"] == ""


# ---------------------------------------------------------------------------
# Score breakdown content
# ---------------------------------------------------------------------------

def test_score_breakdown_records_inputs_and_weights():
    module, _, _ = build_module()
    enrichment = make_enrichment(
        market_potential=0.8, export_readiness=0.7, overall_risk=0.2
    )

    result = run_score(module, enrichment)
    breakdown = result.score_breakdown

    assert breakdown["market_potential"] == pytest.approx(0.8)
    assert breakdown["export_readiness"] == pytest.approx(0.7)
    assert breakdown["risk_score"] == pytest.approx(0.2)
    assert breakdown["weights"] == {
        "market_potential": 0.4,
        "export_readiness": 0.4,
        "risk": 0.2,
    }
    assert breakdown["score"] == pytest.approx(result.score)


# ---------------------------------------------------------------------------
# Input is not mutated
# ---------------------------------------------------------------------------

def test_enrichment_input_is_not_mutated():
    module, _, _ = build_module()
    enrichment = make_enrichment()
    before = enrichment.model_dump()

    run_score(module, enrichment)

    assert enrichment.model_dump() == before


def test_mapping_input_is_not_mutated():
    module, _, _ = build_module()
    enrichment = {
        "market_potential": 0.5,
        "export_readiness": 0.5,
        "risk_assessment": {"overall_risk": 0.5},
    }
    before = copy.deepcopy(enrichment)

    run_score(module, enrichment)

    assert enrichment == before


# ---------------------------------------------------------------------------
# No external API / no OPENAI_API_KEY required
# ---------------------------------------------------------------------------

def test_no_openai_api_key_required(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    module, _, _ = build_module()

    result = run_score(module, make_enrichment())

    assert isinstance(result, ScoringResult)
