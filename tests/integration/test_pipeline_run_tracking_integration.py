"""
Integration test — pipeline_runs lifecycle and counts (Task 27).

Runs the pipeline against a real PostgreSQL database and asserts the
``pipeline_runs`` record's lifecycle:

* the row is created,
* ``status`` transitions to ``completed`` on a successful run,
* ``started_at`` and ``finished_at`` are both populated,
* the persisted counts (``processed_count`` / ``success_count`` /
  ``failed_count``) match the enrichment outcome.

A light failure-lifecycle test is also included: running against a non-existent
CSV path drives the orchestrator's pipeline-level failure branch. That path
*rolls back* the (uncommitted) run row before recording the failure, so no
partial pipeline data is left behind — this test asserts the failed
:class:`PipelineRunResult` and that the database stays clean, without changing
any production code.

Database / safety: identical to the other Task 27 integration modules — the URL
comes from ``DATABASE_URL`` (→ ``SMOKE_TEST_DATABASE_URL``), only a dedicated
test/smoke/ci database is truncated, tables are cleaned before and after each
test, and the mock LLM means no OpenAI key, network or real API call is needed.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest
from sqlalchemy import text
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


def _count(session, table: str) -> int:
    return int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar() or 0)


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


_VALID_ROWS = [
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
]


def _force_mock_mode(monkeypatch, database_url: str) -> None:
    """Pin mock mode + keyless config so no OpenAI client is ever built."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MOCK_LLM_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")


def test_successful_run_lifecycle_and_counts(session_factory, tmp_path, monkeypatch):
    """A successful run is created, completes, and stores matching counts."""
    database_url = get_test_database_url()
    _force_mock_mode(monkeypatch, database_url)

    csv_path = _write_csv(tmp_path / "leads.csv", rows=_VALID_ROWS)

    from src.pipeline.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(session_factory=session_factory)
    result = orchestrator.run(csv_path)

    assert result.status == "completed"

    verify = session_factory()
    try:
        # The run row exists and is the only one.
        assert _count(verify, "pipeline_runs") == 1
        row = verify.execute(
            text(
                "SELECT status, started_at, finished_at, processed_count, "
                "success_count, failed_count "
                "FROM pipeline_runs WHERE pipeline_run_id = :rid"
            ),
            {"rid": result.pipeline_run_id},
        ).one()
        status, started_at, finished_at = row[0], row[1], row[2]
        processed_count, success_count, failed_count = row[3], row[4], row[5]

        # Status transitioned to completed; both timestamps populated.
        assert status == "completed"
        assert started_at is not None
        assert finished_at is not None
        assert finished_at >= started_at

        # Counts match the enrichment outcome (2 valid leads, all enriched).
        assert success_count == result.enriched_records == 2
        assert failed_count == result.failed_enrichments == 0
        assert processed_count == result.enriched_records + result.failed_enrichments
    finally:
        verify.close()


def test_failed_run_lifecycle_leaves_no_partial_data(
    session_factory, tmp_path, monkeypatch
):
    """A pipeline-level failure yields a failed result and a clean database.

    Pointing the orchestrator at a missing CSV makes ingestion raise; the
    orchestrator catches it, rolls back the uncommitted run row and records the
    failure best-effort. The contract this asserts is the failed result and the
    absence of any leftover pipeline data — no production code is modified to
    inject the failure.
    """
    database_url = get_test_database_url()
    _force_mock_mode(monkeypatch, database_url)

    missing_csv = tmp_path / "does_not_exist.csv"
    assert not missing_csv.exists()

    from src.pipeline.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(session_factory=session_factory)
    result = orchestrator.run(missing_csv)

    # The run is reported as failed with an error message, never raised.
    assert result.status == "failed"
    assert result.error_message is not None

    # No partial stage data leaked from the aborted run.
    verify = session_factory()
    try:
        for table in (
            "raw_leads",
            "validated_leads",
            "enrichments",
            "scored_leads",
            "data_quality_reports",
            "validation_errors",
        ):
            assert _count(verify, table) == 0, f"{table} should be empty"
    finally:
        verify.close()
