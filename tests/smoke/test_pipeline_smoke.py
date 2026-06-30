"""
Full-pipeline smoke test with the mock LLM (Task 23).

This test runs the *complete* pipeline end-to-end against the committed sample
data (``data/sample/leads.csv``) on a real, local PostgreSQL instance, using the
deterministic mock LLM (``MOCK_LLM_ENABLED=true``).  It verifies that every
stage table is populated as expected and that the run finishes ``completed``.

It is intentionally skipped unless ``SMOKE_TEST_DATABASE_URL`` points at a local
PostgreSQL database, so the unit suite stays fast, offline and keyless:

    $env:SMOKE_TEST_DATABASE_URL="postgresql+psycopg2://postgres:password@localhost:5432/ai_export_smoke"
    python -m pytest tests/smoke/test_pipeline_smoke.py -v

Safety
------
* No real OpenAI call, no network and no ``OPENAI_API_KEY`` are required — the
  mock provider is used.
* Automatic cleanup (truncation) is only ever performed against a database whose
  URL clearly contains ``test``, ``smoke`` or ``ci``.  The test never drops a
  database and never truncates anything outside the seven known project tables.
* Use a dedicated database such as ``ai_export_smoke`` or ``ai_export_test``.

Schema
------
The schema is created by executing the existing ``migrations/001_initial_schema.sql``
directly through SQLAlchemy (every statement is ``IF NOT EXISTS``), so the schema
definition is never duplicated here and no new migration is introduced.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from src.database.session import get_engine

# --------------------------------------------------------------------------- #
# Paths (resolved from this file so the test is location-independent)
# --------------------------------------------------------------------------- #
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_CSV = _REPO_ROOT / "data" / "sample" / "leads.csv"
_MIGRATION_SQL = _REPO_ROOT / "migrations" / "001_initial_schema.sql"

# The seven project tables, in FK-safe truncation order (CASCADE handles the rest).
_PROJECT_TABLES = (
    "pipeline_runs",
    "raw_leads",
    "validated_leads",
    "enrichments",
    "scored_leads",
    "data_quality_reports",
    "validation_errors",
)

# Tokens that mark a database as a safe, dedicated smoke/test target.
_SAFE_DB_TOKENS = ("test", "smoke", "ci")

# --------------------------------------------------------------------------- #
# Expected outcome for data/sample/leads.csv with current ingestion semantics
# --------------------------------------------------------------------------- #
# The sample CSV has 20 data rows:
#   * 18 schema-valid rows, one of which is an exact business-identity duplicate
#     of another (same company_name / contact_email / product_category /
#     target_market) and is therefore SKIPPED in skip mode,
#   * 2 invalid rows (one missing contact_email, one missing product_category).
#
# So, after one run:
#   raw_leads / validated_leads = 17 (18 valid - 1 duplicate skipped)
#   validation_errors           = 2
#   enrichments (all success)   = 17   (mock LLM never fails)
#   scored_leads                = 17
#
# Note on count consistency: because the duplicate is skipped (not inserted),
# the data_quality_reports row has total_records (= raw_leads = 17) which does
# NOT equal valid_records + invalid_records (17 + 2 = 19).  That difference is
# exactly the one skipped duplicate, and is the documented current behaviour —
# the smoke test asserts the real per-table counts rather than that identity.
_CSV_TOTAL_ROWS = 20
_EXPECTED_INSERTED = 17
_EXPECTED_SKIPPED = 1
_EXPECTED_INVALID = 2
_EXPECTED_ENRICHED = 17
_EXPECTED_SCORED = 17


def _smoke_database_url() -> str | None:
    """Return the configured smoke DB URL, or ``None`` when unset/blank."""
    url = os.environ.get("SMOKE_TEST_DATABASE_URL", "").strip()
    return url or None


def _is_safe_target(database_url: str) -> bool:
    """Only databases whose URL clearly names a test/smoke/ci target are safe."""
    lowered = database_url.lower()
    return any(token in lowered for token in _SAFE_DB_TOKENS)


def _apply_migration(engine) -> None:
    """Create the schema by executing the existing migration SQL verbatim.

    Every statement in ``001_initial_schema.sql`` is ``IF NOT EXISTS`` so this is
    safe to run repeatedly.  ``exec_driver_sql`` sends the whole script straight
    to psycopg2 (which executes multiple ``;``-separated statements) and avoids
    SQLAlchemy's ``:name`` bind-parameter parsing.
    """
    sql = _MIGRATION_SQL.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)


def _truncate_project_tables(engine) -> None:
    """Truncate only the known project tables so the run is repeatable."""
    statement = (
        "TRUNCATE TABLE "
        + ", ".join(_PROJECT_TABLES)
        + " RESTART IDENTITY CASCADE"
    )
    with engine.begin() as conn:
        conn.exec_driver_sql(statement)


def _count(session, table: str) -> int:
    return int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar() or 0)


@pytest.fixture()
def smoke_engine():
    """A SQLAlchemy engine bound to the dedicated smoke database.

    Skips the whole test when ``SMOKE_TEST_DATABASE_URL`` is not set, or when it
    does not clearly point at a dedicated test/smoke/ci database (so production
    data is never touched).
    """
    database_url = _smoke_database_url()
    if database_url is None:
        pytest.skip(
            "SMOKE_TEST_DATABASE_URL is not set; skipping the full-pipeline "
            "PostgreSQL smoke test (set it to a local ai_export_smoke database "
            "to run it)."
        )
    if not _is_safe_target(database_url):
        pytest.skip(
            "SMOKE_TEST_DATABASE_URL does not name a dedicated test database "
            "(its URL must contain 'test', 'smoke' or 'ci' before this test "
            "will truncate and use it). Use e.g. ai_export_smoke."
        )

    engine = get_engine(database_url)
    _apply_migration(engine)
    _truncate_project_tables(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_full_pipeline_smoke_with_mock_llm(smoke_engine, monkeypatch):
    """Run the whole pipeline once and verify every stage table is populated."""
    database_url = _smoke_database_url()

    # Mock LLM, keyless, real local DB — set explicitly so default modules pick
    # them up regardless of any .env file.
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MOCK_LLM_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", ""))

    assert _SAMPLE_CSV.exists(), f"sample CSV missing: {_SAMPLE_CSV}"

    # Real session/repository wiring against the smoke database, injected so the
    # orchestrator reuses our engine rather than the module-level SessionLocal.
    session_factory = sessionmaker(
        bind=smoke_engine, autoflush=False, autocommit=False
    )

    # Imported here so importing the test module has no settings/DB side effects.
    from src.pipeline.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(session_factory=session_factory)
    result = orchestrator.run(_SAMPLE_CSV)

    # --- Run-level result ------------------------------------------------- #
    assert result.status == "completed", f"unexpected status: {result.status}"
    assert result.error_message is None
    assert result.total_records == _CSV_TOTAL_ROWS
    assert result.valid_records == _EXPECTED_INSERTED
    assert result.invalid_records == _EXPECTED_INVALID
    assert result.enriched_records == _EXPECTED_ENRICHED
    assert result.failed_enrichments == 0
    assert result.scored_records == _EXPECTED_SCORED

    # --- Database state (the actual verification Task 23 asks for) --------- #
    verify_session = session_factory()
    try:
        # Exactly one pipeline run, marked completed (the run this test created).
        assert _count(verify_session, "pipeline_runs") == 1
        run_status = verify_session.execute(
            text(
                "SELECT status FROM pipeline_runs "
                "WHERE pipeline_run_id = :rid"
            ),
            {"rid": result.pipeline_run_id},
        ).scalar()
        assert run_status == "completed"

        # Ingestion: duplicate skipped, invalid rows recorded separately.
        assert _count(verify_session, "raw_leads") == _EXPECTED_INSERTED
        assert _count(verify_session, "validated_leads") == _EXPECTED_INSERTED
        assert _count(verify_session, "validation_errors") == _EXPECTED_INVALID

        # Enrichment: every validated lead enriched successfully under mock LLM.
        assert _count(verify_session, "enrichments") == _EXPECTED_ENRICHED
        success_enrichments = verify_session.execute(
            text(
                "SELECT count(*) FROM enrichments "
                "WHERE enrichment_status = 'success'"
            )
        ).scalar()
        assert int(success_enrichments) == _EXPECTED_ENRICHED

        # Scoring: every successful enrichment scored.
        assert _count(verify_session, "scored_leads") == _EXPECTED_SCORED

        # Exactly one data quality report for this run, with the observed counts.
        assert _count(verify_session, "data_quality_reports") == 1
        report = verify_session.execute(
            text(
                "SELECT total_records, valid_records, invalid_records, "
                "enriched_records, failed_enrichments, scored_records "
                "FROM data_quality_reports WHERE pipeline_run_id = :rid"
            ),
            {"rid": result.pipeline_run_id},
        ).one()
        total_records, valid_records, invalid_records = report[0], report[1], report[2]
        enriched_records, failed_enrichments, scored_records = (
            report[3],
            report[4],
            report[5],
        )
        assert total_records == _EXPECTED_INSERTED
        assert valid_records == _EXPECTED_INSERTED
        assert invalid_records == _EXPECTED_INVALID
        assert enriched_records == _EXPECTED_ENRICHED
        assert failed_enrichments == 0
        assert scored_records == _EXPECTED_SCORED
    finally:
        verify_session.close()
