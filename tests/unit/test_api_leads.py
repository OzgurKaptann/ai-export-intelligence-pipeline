"""
Unit tests for the FastAPI leads routes (Task 20).

These tests run in complete isolation:

* No live database — the ``get_repository`` dependency is overridden with a fake
  repository, so ``get_db`` is never called and no ``DATABASE_URL`` is needed.
* No OpenAI, no network and no ``OPENAI_API_KEY``.
* The route-ordering guarantee (``/leads/filter`` before ``/leads/{lead_id}``)
  is verified so ``filter`` is not swallowed as a UUID path parameter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes.leads import get_repository


def make_scored_lead(score: float = 80.0):
    """Build a fake ORM-like scored lead object with all response fields."""
    return SimpleNamespace(
        scored_lead_id=str(uuid4()),
        validated_lead_id=str(uuid4()),
        enrichment_id=str(uuid4()),
        pipeline_run_id=str(uuid4()),
        company_name="Acme Export Co",
        product_category="electronics",
        score=score,
        score_breakdown={"market_potential": 0.8, "export_readiness": 0.7},
        scored_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class FakeRepository:
    """Records calls and returns canned data — no database involved."""

    def __init__(self, scored_leads=None, lead_by_id=None):
        self._scored_leads = scored_leads if scored_leads is not None else []
        self._lead_by_id = lead_by_id
        self.get_scored_leads_calls = []
        self.get_scored_lead_by_id_calls = []

    def get_scored_leads(self, min_score=None):
        self.get_scored_leads_calls.append(min_score)
        return self._scored_leads

    def get_scored_lead_by_id(self, scored_lead_id):
        self.get_scored_lead_by_id_calls.append(scored_lead_id)
        return self._lead_by_id


@pytest.fixture
def client_with_repo():
    """Yield a (TestClient, fake_repo) pair with get_repository overridden."""

    def _make(fake_repo):
        app.dependency_overrides[get_repository] = lambda: fake_repo
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(get_repository, None)


# ---------------------------------------------------------------------------
# GET /leads
# ---------------------------------------------------------------------------

def test_list_leads_returns_200_and_json_list(client_with_repo):
    repo = FakeRepository(scored_leads=[make_scored_lead(), make_scored_lead(90.0)])
    client = client_with_repo(repo)

    response = client.get("/leads")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2


def test_list_leads_returns_scored_lead_shaped_records(client_with_repo):
    lead = make_scored_lead()
    repo = FakeRepository(scored_leads=[lead])
    client = client_with_repo(repo)

    body = client.get("/leads").json()
    record = body[0]

    expected_fields = {
        "scored_lead_id",
        "validated_lead_id",
        "enrichment_id",
        "pipeline_run_id",
        "company_name",
        "product_category",
        "score",
        "score_breakdown",
        "scored_at",
    }
    assert set(record.keys()) == expected_fields
    assert record["company_name"] == lead.company_name
    assert record["score"] == lead.score


def test_list_leads_calls_get_scored_leads(client_with_repo):
    repo = FakeRepository(scored_leads=[])
    client = client_with_repo(repo)

    client.get("/leads")

    assert repo.get_scored_leads_calls == [None]


def test_list_leads_passes_min_score_to_repository(client_with_repo):
    repo = FakeRepository(scored_leads=[])
    client = client_with_repo(repo)

    client.get("/leads", params={"min_score": 75})

    assert repo.get_scored_leads_calls == [75.0]


# ---------------------------------------------------------------------------
# GET /leads/filter  (must NOT be swallowed by /leads/{lead_id})
# ---------------------------------------------------------------------------

def test_filter_endpoint_is_not_swallowed_by_lead_id(client_with_repo):
    repo = FakeRepository(scored_leads=[make_scored_lead()])
    client = client_with_repo(repo)

    response = client.get("/leads/filter")

    # If "filter" were treated as a {lead_id} UUID, this would be a 422.
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    # The list endpoint was hit, not the by-id endpoint.
    assert repo.get_scored_leads_calls == [None]
    assert repo.get_scored_lead_by_id_calls == []


def test_filter_endpoint_passes_min_score_to_repository(client_with_repo):
    repo = FakeRepository(scored_leads=[])
    client = client_with_repo(repo)

    client.get("/leads/filter", params={"min_score": 80})

    assert repo.get_scored_leads_calls == [80.0]


# ---------------------------------------------------------------------------
# GET /leads/{lead_id}
# ---------------------------------------------------------------------------

def test_get_lead_by_id_returns_single_lead(client_with_repo):
    lead = make_scored_lead()
    repo = FakeRepository(lead_by_id=lead)
    client = client_with_repo(repo)

    response = client.get(f"/leads/{lead.scored_lead_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["scored_lead_id"] == lead.scored_lead_id
    assert body["company_name"] == lead.company_name


def test_get_lead_by_id_calls_get_scored_lead_by_id(client_with_repo):
    lead = make_scored_lead()
    repo = FakeRepository(lead_by_id=lead)
    client = client_with_repo(repo)

    client.get(f"/leads/{lead.scored_lead_id}")

    assert repo.get_scored_lead_by_id_calls == [lead.scored_lead_id]


def test_get_missing_lead_returns_404(client_with_repo):
    repo = FakeRepository(lead_by_id=None)
    client = client_with_repo(repo)

    response = client.get(f"/leads/{uuid4()}")

    assert response.status_code == 404


def test_invalid_uuid_returns_validation_error(client_with_repo):
    repo = FakeRepository(lead_by_id=None)
    client = client_with_repo(repo)

    response = client.get("/leads/not-a-uuid")

    assert response.status_code == 422
    # The repository must not be consulted for a malformed UUID.
    assert repo.get_scored_lead_by_id_calls == []


# ---------------------------------------------------------------------------
# Isolation guarantees
# ---------------------------------------------------------------------------

def test_endpoints_do_not_require_database_url(client_with_repo, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = FakeRepository(scored_leads=[make_scored_lead()])
    client = client_with_repo(repo)

    assert client.get("/leads").status_code == 200


def test_endpoints_do_not_require_openai_api_key(client_with_repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    repo = FakeRepository(scored_leads=[make_scored_lead()])
    client = client_with_repo(repo)

    assert client.get("/leads").status_code == 200


def test_leads_routes_still_registered():
    # The /leads routes must remain registered alongside the Task 21 additions.
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert any(p.startswith("/leads") for p in paths)


def test_health_still_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
