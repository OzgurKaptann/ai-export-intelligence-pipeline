"""
Integration test — all six FastAPI routes over a real PostgreSQL (Task 27).

Uses :class:`fastapi.testclient.TestClient` with the application's ``get_db``
dependency overridden to yield sessions bound to the integration database, then
seeds data by running the full pipeline (mock LLM) and exercises every route:

1. ``GET /health``
2. ``GET /leads``
3. ``GET /leads/filter``
4. ``GET /leads/{lead_id}``
5. ``GET /pipeline-runs``
6. ``GET /pipeline-runs/{run_id}/report``

404 behaviour for a missing lead and a missing report is also checked.

Database / safety: identical to the other Task 27 integration modules — URL from
``DATABASE_URL`` (→ ``SMOKE_TEST_DATABASE_URL``), only a dedicated test/smoke/ci
database is truncated, tables cleaned before and after each test, mock LLM only
(no OpenAI key, network or real API call). The dependency override is always
removed afterwards so no global app state leaks between tests.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from src.database.session import get_engine

# --------------------------------------------------------------------------- #
# Local integration helpers (kept in-file per Task 27).
# --------------------------------------------------------------------------- #
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = _REPO_ROOT / "migrations" / "001_initial_schema.sql"

_PROJECT_TABLES = (
    "pipeline_runs",
    "raw_leads",
    "validated_leads",
    "enrichments",
    "scored_leads",
    "data_quality_reports",
    "validation_errors",
)

_SAFE_DB_TOKENS = ("test", "smoke", "ci")

_CSV_HEADER = (
    "company_name",
    "contact_email",
    "contact_phone",
    "product_category",
    "annual_revenue",
    "target_market",
)


def get_test_database_url() -> str | None:
    url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("SMOKE_TEST_DATABASE_URL")
        or ""
    ).strip()
    return url or None


def _is_safe_target(database_url: str) -> bool:
    lowered = database_url.lower()
    return any(token in lowered for token in _SAFE_DB_TOKENS)


def run_migration_sql_if_needed(engine) -> None:
    sql = _MIGRATION_SQL.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)


def truncate_project_tables(engine) -> None:
    statement = (
        "TRUNCATE TABLE "
        + ", ".join(_PROJECT_TABLES)
        + " RESTART IDENTITY CASCADE"
    )
    with engine.begin() as conn:
        conn.exec_driver_sql(statement)


def _write_csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in _CSV_HEADER})
    return path


@pytest.fixture()
def db_engine():
    database_url = get_test_database_url()
    if database_url is None:
        pytest.skip(
            "DATABASE_URL is not set; skipping PostgreSQL integration tests "
            "(set DATABASE_URL to a dedicated test/smoke/ci database to run them)."
        )
    if not _is_safe_target(database_url):
        pytest.skip(
            "DATABASE_URL does not name a dedicated test database (its URL must "
            "contain 'test', 'smoke' or 'ci'); refusing to truncate it."
        )

    engine = get_engine(database_url)
    run_migration_sql_if_needed(engine)
    truncate_project_tables(engine)
    try:
        yield engine
    finally:
        truncate_project_tables(engine)
        engine.dispose()


@pytest.fixture()
def session_factory(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


_SEED_ROWS = [
    {
        "company_name": "Aurora Coatings",
        "contact_email": "export@aurora.example",
        "contact_phone": "+90 212 555 0101",
        "product_category": "Industrial Coatings",
        "annual_revenue": "4200000",
        "target_market": "EU",
    },
    {
        "company_name": "Bosphorus Textiles",
        "contact_email": "sales@bosphorus.example",
        "contact_phone": "+90 212 555 0102",
        "product_category": "Textiles",
        "annual_revenue": "1875000",
        "target_market": "Middle East",
    },
    {
        "company_name": "Cedar Valley Organics",
        "contact_email": "trade@cedarvalley.example",
        "contact_phone": "+1 503 555 0103",
        "product_category": "Organic Foods",
        "annual_revenue": "990000",
        "target_market": "North America",
    },
]


@pytest.fixture()
def seeded_client(session_factory, tmp_path, monkeypatch):
    """A TestClient wired to the integration DB, pre-seeded by a pipeline run.

    Overrides the app's ``get_db`` dependency so every route uses the integration
    session, runs the pipeline once to populate scored leads / runs / reports,
    and yields the client plus the seeded ``pipeline_run_id``. The override is
    always cleared afterwards so app state never leaks between tests.
    """
    database_url = get_test_database_url()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MOCK_LLM_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    csv_path = _write_csv(tmp_path / "leads.csv", rows=_SEED_ROWS)

    from src.pipeline.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(session_factory=session_factory)
    result = orchestrator.run(csv_path)
    assert result.status == "completed"

    from src.api.main import app
    from src.database.session import get_db

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as client:
            yield client, result.pipeline_run_id
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_health_endpoint(seeded_client):
    """1. GET /health returns 200 and the deterministic status payload."""
    client, _ = seeded_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_leads_returns_list(seeded_client):
    """2. GET /leads returns a non-empty JSON list of scored leads."""
    client, _ = seeded_client
    response = client.get("/leads")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == len(_SEED_ROWS)
    assert {"scored_lead_id", "score", "company_name"} <= set(body[0].keys())


def test_filter_leads_min_score(seeded_client):
    """3. GET /leads/filter applies the min_score filter correctly."""
    client, _ = seeded_client
    everything = client.get("/leads").json()
    scores = sorted(lead["score"] for lead in everything)
    # A threshold at the median keeps only the higher-scoring leads.
    threshold = scores[len(scores) // 2]

    response = client.get("/leads/filter", params={"min_score": threshold})
    assert response.status_code == 200
    filtered = response.json()
    assert isinstance(filtered, list)
    assert all(lead["score"] >= threshold for lead in filtered)
    assert len(filtered) <= len(everything)


def test_get_lead_by_id_returns_correct_lead(seeded_client):
    """4a. GET /leads/{lead_id} returns the lead with the requested id."""
    client, _ = seeded_client
    target = client.get("/leads").json()[0]
    lead_id = target["scored_lead_id"]

    response = client.get(f"/leads/{lead_id}")
    assert response.status_code == 200
    assert response.json()["scored_lead_id"] == lead_id


def test_get_missing_lead_returns_404(seeded_client):
    """4b. GET /leads/{lead_id} returns 404 for an unknown UUID."""
    client, _ = seeded_client
    response = client.get(f"/leads/{uuid4()}")
    assert response.status_code == 404


def test_list_pipeline_runs_returns_records(seeded_client):
    """5. GET /pipeline-runs lists the seeded run."""
    client, run_id = seeded_client
    response = client.get("/pipeline-runs")
    assert response.status_code == 200
    runs = response.json()
    assert isinstance(runs, list)
    assert any(run["pipeline_run_id"] == run_id for run in runs)


def test_run_report_endpoint_returns_quality_report(seeded_client):
    """6a. GET /pipeline-runs/{run_id}/report returns the generated report."""
    client, run_id = seeded_client
    response = client.get(f"/pipeline-runs/{run_id}/report")
    assert response.status_code == 200
    report = response.json()
    assert report["pipeline_run_id"] == run_id
    # The report reflects the seeded pipeline outcome.
    assert report["valid_records"] == len(_SEED_ROWS)
    assert report["scored_records"] == len(_SEED_ROWS)


def test_run_report_missing_returns_404(seeded_client):
    """6b. GET /pipeline-runs/{run_id}/report returns 404 for an unknown run."""
    client, _ = seeded_client
    response = client.get(f"/pipeline-runs/{uuid4()}/report")
    assert response.status_code == 404
