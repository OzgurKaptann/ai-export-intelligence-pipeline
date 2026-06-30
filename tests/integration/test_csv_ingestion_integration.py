"""
Integration test — full CSV ingestion flow against a real PostgreSQL (Task 27).

Exercises :func:`src.ingestion.csv_ingestion.ingest_csv_file` end-to-end on a
real PostgreSQL database (never SQLite), verifying that:

* valid, non-duplicate rows land in ``raw_leads`` **and** ``validated_leads``,
* an invalid row (missing required ``contact_email``) is recorded only in
  ``validation_errors``,
* a row whose business identity already exists is SKIPPED in the current
  skip-mode behaviour (it never violates the ``raw_leads.idempotency_key``
  unique constraint),
* the returned :class:`IngestionResult` counts match the database state,
* the ``idempotency_key`` uniqueness invariant holds.

Database
--------
The database URL is read from the environment (``DATABASE_URL``, falling back to
``SMOKE_TEST_DATABASE_URL``). When neither is set the whole module is skipped
with a clear reason, so the offline unit suite is never affected. As an extra
safeguard the URL must name a dedicated ``test`` / ``smoke`` / ``ci`` database
before any table is truncated. Tables are truncated before *and* after each test
so no test data leaks between tests or out of the run.

Safety
------
No real OpenAI call, no network and no ``OPENAI_API_KEY`` are involved — ingestion
is pure validation + persistence and never touches the LLM layer.

update / reprocess idempotency modes
------------------------------------
Only skip mode is implemented in the current pipeline. The ``update`` and
``reprocess`` modes are therefore covered by explicitly *skipped* tests with a
clear reason rather than asserting behaviour that does not exist — keeping the
suite honest without adding production features.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from src.database.repository import PipelineRepository
from src.database.session import get_engine
from src.ingestion.csv_ingestion import ingest_csv_file

# --------------------------------------------------------------------------- #
# Local integration helpers (kept in-file per Task 27 to avoid a shared
# conftest; identical small helpers are repeated across the five test modules).
# --------------------------------------------------------------------------- #
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = _REPO_ROOT / "migrations" / "001_initial_schema.sql"

# The seven project tables; CASCADE + RESTART IDENTITY makes truncation total.
_PROJECT_TABLES = (
    "pipeline_runs",
    "raw_leads",
    "validated_leads",
    "enrichments",
    "scored_leads",
    "data_quality_reports",
    "validation_errors",
)

# Tokens that mark a database as a safe, dedicated test/smoke/ci target.
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
    """Return the integration DB URL from the environment, or ``None``."""
    url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("SMOKE_TEST_DATABASE_URL")
        or ""
    ).strip()
    return url or None


def _is_safe_target(database_url: str) -> bool:
    """Only databases whose URL clearly names a test/smoke/ci target are safe."""
    lowered = database_url.lower()
    return any(token in lowered for token in _SAFE_DB_TOKENS)


def run_migration_sql_if_needed(engine) -> None:
    """Apply the existing migration SQL verbatim (every statement IF NOT EXISTS)."""
    sql = _MIGRATION_SQL.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)


def truncate_project_tables(engine) -> None:
    """Truncate only the known project tables so each test starts clean."""
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
    """Write *rows* to *path* as a UTF-8 CSV with the standard lead header."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in _CSV_HEADER})
    return path


@pytest.fixture()
def db_engine():
    """SQLAlchemy engine bound to the dedicated integration database.

    Skips the whole module when no ``DATABASE_URL`` (or ``SMOKE_TEST_DATABASE_URL``)
    is configured, or when it does not clearly point at a test/smoke/ci database,
    so production data is never truncated. Truncates the project tables on entry
    and on exit so no test data leaks in either direction.
    """
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
    """A sessionmaker bound to the integration engine."""
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _seed_pipeline_run(session_factory, file_path: str) -> str:
    """Create one ``pipeline_runs`` row (the FK parent for ingested rows)."""
    run_id = str(uuid4())
    session = session_factory()
    try:
        repository = PipelineRepository(session)
        repository.create_pipeline_run(
            pipeline_run_id=run_id,
            started_at=datetime.now(timezone.utc),
            file_path=file_path,
            status="in_progress",
        )
        session.commit()
    finally:
        session.close()
    return run_id


# --------------------------------------------------------------------------- #
# Skip-mode ingestion (the behaviour that is actually implemented)
# --------------------------------------------------------------------------- #


def test_ingestion_persists_valid_invalid_and_skips_duplicate(
    session_factory, tmp_path
):
    """Valid rows persist, the invalid row is recorded, the duplicate is skipped."""
    csv_path = _write_csv(
        tmp_path / "leads.csv",
        rows=[
            # Two valid, distinct business identities.
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
            # Invalid: missing the required contact_email.
            {
                "company_name": "Summit Grain Traders",
                "contact_email": "",
                "contact_phone": "+1 312 555 0119",
                "product_category": "Grain",
                "annual_revenue": "3300000",
                "target_market": "Africa",
            },
            # Duplicate business identity of the first row (same company / email /
            # product_category / target_market) — skipped in skip mode.
            {
                "company_name": "Aurora Coatings",
                "contact_email": "export@aurora.example",
                "contact_phone": "+90 212 555 0199",
                "product_category": "Industrial Coatings",
                "annual_revenue": "4350000",
                "target_market": "EU",
            },
        ],
    )

    run_id = _seed_pipeline_run(session_factory, str(csv_path))

    session = session_factory()
    try:
        repository = PipelineRepository(session)
        result = ingest_csv_file(str(csv_path), run_id, repository)
        session.commit()
    finally:
        session.close()

    # --- Returned IngestionResult counts ------------------------------------ #
    assert result.total == 4
    assert result.inserted == 2
    assert result.skipped == 1
    assert result.failed == 1

    # --- Database state matches the returned counts ------------------------- #
    verify = session_factory()
    try:
        assert _count(verify, "raw_leads") == result.inserted
        assert _count(verify, "validated_leads") == result.inserted
        assert _count(verify, "validation_errors") == result.failed

        # idempotency_key uniqueness: exactly two distinct keys, one per inserted
        # identity, and never a duplicate row for the skipped identity.
        distinct_keys = verify.execute(
            text("SELECT count(DISTINCT idempotency_key) FROM raw_leads")
        ).scalar()
        assert int(distinct_keys) == result.inserted

        # The recorded validation error attributes the missing required field.
        error_field = verify.execute(
            text("SELECT error_field FROM validation_errors")
        ).scalar()
        assert error_field == "contact_email"
    finally:
        verify.close()


def test_reingesting_same_identities_skips_all_in_skip_mode(
    session_factory, tmp_path
):
    """A second ingestion of already-ingested identities skips every row.

    Skip-mode duplicate detection is keyed on the global ``raw_leads.idempotency_key``
    lookup, so re-running ingestion (even under a different pipeline run) adds no
    new ``raw_leads`` rows and reports every valid row as ``skipped``.
    """
    rows = [
        {
            "company_name": "Cedar Valley Organics",
            "contact_email": "trade@cedarvalley.example",
            "contact_phone": "+1 503 555 0103",
            "product_category": "Organic Foods",
            "annual_revenue": "990000",
            "target_market": "North America",
        },
        {
            "company_name": "Delta Marine",
            "contact_email": "info@delta-marine.example",
            "contact_phone": "+65 6555 0104",
            "product_category": "Marine Equipment",
            "annual_revenue": "6350000",
            "target_market": "Southeast Asia",
        },
    ]
    csv_path = _write_csv(tmp_path / "leads.csv", rows=rows)

    first_run = _seed_pipeline_run(session_factory, str(csv_path))
    session = session_factory()
    try:
        first = ingest_csv_file(str(csv_path), first_run, PipelineRepository(session))
        session.commit()
    finally:
        session.close()

    assert first.inserted == 2
    assert first.skipped == 0

    second_run = _seed_pipeline_run(session_factory, str(csv_path))
    session = session_factory()
    try:
        second = ingest_csv_file(
            str(csv_path), second_run, PipelineRepository(session)
        )
        session.commit()
    finally:
        session.close()

    assert second.inserted == 0
    assert second.skipped == 2

    verify = session_factory()
    try:
        # Still only the two originally-ingested rows; no duplicates were created.
        assert _count(verify, "raw_leads") == 2
        assert _count(verify, "validated_leads") == 2
    finally:
        verify.close()


# --------------------------------------------------------------------------- #
# update / reprocess modes — not implemented in the current pipeline
# --------------------------------------------------------------------------- #


@pytest.mark.skip(
    reason="update/reprocess idempotency modes are not implemented in the "
    "current pipeline yet; only skip mode exists."
)
def test_update_idempotency_mode_overwrites_existing_lead():
    """Placeholder: update mode would overwrite an existing identity in place."""


@pytest.mark.skip(
    reason="update/reprocess idempotency modes are not implemented in the "
    "current pipeline yet; only skip mode exists."
)
def test_reprocess_idempotency_mode_reenriches_existing_lead():
    """Placeholder: reprocess mode would re-run enrichment for an existing lead."""
