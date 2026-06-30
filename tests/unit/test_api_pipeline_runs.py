"""
Unit tests for the FastAPI pipeline runs and quality report routes (Task 21).

These tests run in complete isolation:

* No live database — the ``get_session`` and ``get_repository`` dependencies are
  overridden with fakes, so ``get_db`` is never called and no ``DATABASE_URL``
  is needed.
* No OpenAI, no network and no ``OPENAI_API_KEY``.

Covered:

* ``GET /pipeline-runs`` returns a 200 JSON list of ``PipelineRunResponse``-shaped
  records, ordered by ``started_at`` descending (as the query returns them).
* ``GET /pipeline-runs/{run_id}/report`` returns a 200 JSON report, calls
  ``repository.get_quality_report(run_id)``, returns 404 when missing and 422 for
  a malformed UUID.
* The existing ``/health`` and ``/leads`` routes remain registered.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes.pipeline_runs import get_repository, get_session


def make_pipeline_run(started_at: datetime, status: str = "completed"):
    """Build a fake ORM-like pipeline run object with all response fields."""
    return SimpleNamespace(
        pipeline_run_id=str(uuid4()),
        status=status,
        started_at=started_at,
        finished_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        processed_count=10,
        success_count=8,
        failed_count=2,
        file_path="data/sample/leads.csv",
        run_metadata={"source": "unit-test"},
    )


def make_quality_report(pipeline_run_id: str):
    """Build a fake ORM-like data quality report with all response fields."""
    return SimpleNamespace(
        report_id=str(uuid4()),
        pipeline_run_id=pipeline_run_id,
        total_records=10,
        valid_records=9,
        invalid_records=1,
        enriched_records=8,
        failed_enrichments=1,
        scored_records=8,
        created_at=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
    )


class FakeScalarResult:
    """Mimics the object returned by ``Session.scalars`` for ``.all()``."""

    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class FakeSession:
    """Returns canned pipeline runs from ``scalars`` — no database involved."""

    def __init__(self, runs=None):
        self._runs = runs if runs is not None else []
        self.scalars_calls = 0

    def scalars(self, statement):
        # The route builds an ORDER BY started_at DESC statement; the fake simply
        # returns the canned list it was given (already in the expected order).
        self.scalars_calls += 1
        return FakeScalarResult(self._runs)


class FakeRepository:
    """Records calls and returns a canned report — no database involved."""

    def __init__(self, report=None):
        self._report = report
        self.get_quality_report_calls = []

    def get_quality_report(self, pipeline_run_id):
        self.get_quality_report_calls.append(pipeline_run_id)
        return self._report


@pytest.fixture
def client_with_overrides():
    """Yield a factory that overrides session/repository dependencies."""

    def _make(fake_session=None, fake_repo=None):
        if fake_session is not None:
            app.dependency_overrides[get_session] = lambda: fake_session
        if fake_repo is not None:
            app.dependency_overrides[get_repository] = lambda: fake_repo
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_repository, None)


# ---------------------------------------------------------------------------
# GET /pipeline-runs
# ---------------------------------------------------------------------------

def test_list_pipeline_runs_returns_200_and_json_list(client_with_overrides):
    runs = [
        make_pipeline_run(datetime(2026, 2, 1, tzinfo=timezone.utc)),
        make_pipeline_run(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ]
    client = client_with_overrides(fake_session=FakeSession(runs=runs))

    response = client.get("/pipeline-runs")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2


def test_list_pipeline_runs_returns_pipeline_run_shaped_records(
    client_with_overrides,
):
    run = make_pipeline_run(datetime(2026, 2, 1, tzinfo=timezone.utc))
    client = client_with_overrides(fake_session=FakeSession(runs=[run]))

    record = client.get("/pipeline-runs").json()[0]

    expected_fields = {
        "pipeline_run_id",
        "status",
        "started_at",
        "finished_at",
        "processed_count",
        "success_count",
        "failed_count",
        "file_path",
        "run_metadata",
    }
    assert set(record.keys()) == expected_fields
    assert record["pipeline_run_id"] == run.pipeline_run_id
    assert record["status"] == run.status
    assert record["processed_count"] == run.processed_count


def test_list_pipeline_runs_orders_by_started_at_desc(client_with_overrides):
    newer = make_pipeline_run(datetime(2026, 3, 1, tzinfo=timezone.utc))
    older = make_pipeline_run(datetime(2026, 1, 1, tzinfo=timezone.utc))
    # The query orders by started_at DESC; the route returns runs in the order
    # the session yields them, so a newest-first list must be preserved.
    client = client_with_overrides(fake_session=FakeSession(runs=[newer, older]))

    body = client.get("/pipeline-runs").json()

    returned = [r["started_at"] for r in body]
    assert returned == sorted(returned, reverse=True)
    assert body[0]["pipeline_run_id"] == newer.pipeline_run_id


def test_list_pipeline_runs_empty_returns_empty_list(client_with_overrides):
    client = client_with_overrides(fake_session=FakeSession(runs=[]))

    response = client.get("/pipeline-runs")

    assert response.status_code == 200
    assert response.json() == []


def test_list_pipeline_runs_does_not_require_database_url(
    client_with_overrides, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    run = make_pipeline_run(datetime(2026, 2, 1, tzinfo=timezone.utc))
    client = client_with_overrides(fake_session=FakeSession(runs=[run]))

    assert client.get("/pipeline-runs").status_code == 200


def test_list_pipeline_runs_does_not_require_openai_api_key(
    client_with_overrides, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    run = make_pipeline_run(datetime(2026, 2, 1, tzinfo=timezone.utc))
    client = client_with_overrides(fake_session=FakeSession(runs=[run]))

    assert client.get("/pipeline-runs").status_code == 200


# ---------------------------------------------------------------------------
# GET /pipeline-runs/{run_id}/report
# ---------------------------------------------------------------------------

def test_get_run_report_returns_200_and_json_report(client_with_overrides):
    run_id = str(uuid4())
    report = make_quality_report(run_id)
    repo = FakeRepository(report=report)
    client = client_with_overrides(fake_repo=repo)

    response = client.get(f"/pipeline-runs/{run_id}/report")

    assert response.status_code == 200
    body = response.json()
    assert body["report_id"] == report.report_id
    assert body["pipeline_run_id"] == run_id
    assert body["total_records"] == report.total_records


def test_get_run_report_has_expected_shape(client_with_overrides):
    run_id = str(uuid4())
    repo = FakeRepository(report=make_quality_report(run_id))
    client = client_with_overrides(fake_repo=repo)

    body = client.get(f"/pipeline-runs/{run_id}/report").json()

    expected_fields = {
        "report_id",
        "pipeline_run_id",
        "total_records",
        "valid_records",
        "invalid_records",
        "enriched_records",
        "failed_enrichments",
        "scored_records",
        "created_at",
    }
    assert set(body.keys()) == expected_fields


def test_get_run_report_calls_get_quality_report(client_with_overrides):
    run_id = str(uuid4())
    repo = FakeRepository(report=make_quality_report(run_id))
    client = client_with_overrides(fake_repo=repo)

    client.get(f"/pipeline-runs/{run_id}/report")

    assert repo.get_quality_report_calls == [run_id]


def test_get_run_report_missing_returns_404(client_with_overrides):
    repo = FakeRepository(report=None)
    client = client_with_overrides(fake_repo=repo)

    response = client.get(f"/pipeline-runs/{uuid4()}/report")

    assert response.status_code == 404


def test_get_run_report_invalid_uuid_returns_validation_error(
    client_with_overrides,
):
    repo = FakeRepository(report=None)
    client = client_with_overrides(fake_repo=repo)

    response = client.get("/pipeline-runs/not-a-uuid/report")

    assert response.status_code == 422
    # The repository must not be consulted for a malformed UUID.
    assert repo.get_quality_report_calls == []


def test_get_run_report_does_not_require_database_url(
    client_with_overrides, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    run_id = str(uuid4())
    repo = FakeRepository(report=make_quality_report(run_id))
    client = client_with_overrides(fake_repo=repo)

    assert client.get(f"/pipeline-runs/{run_id}/report").status_code == 200


def test_get_run_report_does_not_require_openai_api_key(
    client_with_overrides, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    run_id = str(uuid4())
    repo = FakeRepository(report=make_quality_report(run_id))
    client = client_with_overrides(fake_repo=repo)

    assert client.get(f"/pipeline-runs/{run_id}/report").status_code == 200


# ---------------------------------------------------------------------------
# Coexistence with existing routes / no scope creep
# ---------------------------------------------------------------------------

def test_health_still_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_leads_routes_still_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert any(p.startswith("/leads") for p in paths)


def test_pipeline_runs_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert any(p.startswith("/pipeline-runs") for p in paths)


def test_no_dashboard_or_docker_routes_added():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert not any(p.startswith("/dashboard") for p in paths)
    assert not any("docker" in p for p in paths)
