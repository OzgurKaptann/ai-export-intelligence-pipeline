"""
Unit tests for ``src/pipeline/data_quality.generate_report`` (Task 18).

Strategy
--------
Two complementary, fully offline approaches — no PostgreSQL, no Docker, no
OpenAI, no network, no ``OPENAI_API_KEY``:

* **SQLite in-memory** (mirroring ``tests/unit/test_repository.py``) exercises
  the real count queries and the real ``PipelineRepository.insert_quality_report``
  persistence path.  PostgreSQL-only column types are sidestepped by creating
  plain-SQL tables that mirror the columns the report touches.
* **A fake session + fake repository** prove that ``generate_report`` reuses the
  injected session (never creating one), maps every count onto the result and
  persists through the repository — all without any database at all.

The data inserted for the "consistent" cases is constructed so that every raw
lead is either validated or recorded as a validation error, which is exactly the
condition under which ``valid_records + invalid_records == total_records``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.database.repository import PipelineRepository
from src.pipeline.data_quality import DataQualityReportResult, generate_report


# ---------------------------------------------------------------------------
# SQLite-compatible schema (only the tables the report reads / writes)
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS raw_leads (
    raw_lead_id       TEXT PRIMARY KEY,
    idempotency_key   TEXT NOT NULL UNIQUE,
    pipeline_run_id   TEXT NOT NULL,
    company_name      TEXT NOT NULL,
    contact_email     TEXT NOT NULL,
    contact_phone     TEXT,
    product_category  TEXT NOT NULL,
    annual_revenue    REAL,
    target_market     TEXT,
    raw_csv_row       TEXT NOT NULL,
    ingested_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validated_leads (
    validated_lead_id TEXT PRIMARY KEY,
    raw_lead_id       TEXT NOT NULL,
    pipeline_run_id   TEXT NOT NULL,
    company_name      TEXT NOT NULL,
    contact_email     TEXT NOT NULL,
    contact_phone     TEXT,
    product_category  TEXT NOT NULL,
    annual_revenue    REAL,
    target_market     TEXT,
    validated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enrichments (
    enrichment_id         TEXT PRIMARY KEY,
    validated_lead_id     TEXT NOT NULL,
    pipeline_run_id       TEXT NOT NULL,
    enrichment_status     TEXT NOT NULL,
    market_potential      REAL,
    export_readiness      REAL,
    risk_assessment       TEXT,
    recommended_markets   TEXT,
    confidence_score      REAL,
    error_type            TEXT,
    error_message         TEXT,
    failed_at             TEXT,
    retry_count           INTEGER NOT NULL DEFAULT 0,
    raw_llm_response      TEXT,
    prompt_version        TEXT NOT NULL,
    model_name            TEXT NOT NULL,
    enrichment_created_at TEXT
);

CREATE TABLE IF NOT EXISTS scored_leads (
    scored_lead_id    TEXT PRIMARY KEY,
    validated_lead_id TEXT NOT NULL,
    enrichment_id     TEXT NOT NULL,
    pipeline_run_id   TEXT NOT NULL,
    company_name      TEXT NOT NULL,
    product_category  TEXT NOT NULL,
    score             REAL NOT NULL,
    score_breakdown   TEXT NOT NULL,
    scored_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_reports (
    report_id           TEXT PRIMARY KEY,
    pipeline_run_id     TEXT NOT NULL UNIQUE,
    total_records       INTEGER NOT NULL,
    valid_records       INTEGER NOT NULL,
    invalid_records     INTEGER NOT NULL,
    enriched_records    INTEGER NOT NULL,
    failed_enrichments  INTEGER NOT NULL,
    scored_records      INTEGER NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_errors (
    error_id        TEXT PRIMARY KEY,
    pipeline_run_id TEXT NOT NULL,
    raw_lead_id     TEXT,
    error_stage     TEXT NOT NULL,
    error_field     TEXT,
    error_message   TEXT NOT NULL,
    recorded_at     TEXT NOT NULL
);
"""

RUN_ID = "11111111-1111-1111-1111-111111111111"
OTHER_RUN_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="function")
def session():
    """Fresh SQLite in-memory session with the report-relevant tables created."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
        conn.commit()

    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    sess = factory()
    yield sess
    sess.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Insert helpers — go through PipelineRepository (the ORM path) so writes and
# the report's count reads share identical UUID type processing.  (Raw-SQL
# inserts would store hyphenated ids while the ORM count binds hyphen-stripped
# ones, so the two would never match on SQLite.)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _seed(
    session,
    run_id: str = RUN_ID,
    *,
    raw=0,
    validated=0,
    invalid=0,
    enriched=0,
    failed=0,
    scored=0,
) -> None:
    """Insert the requested number of rows in each table for *run_id*."""
    repo = PipelineRepository(session)
    for _ in range(raw):
        repo.insert_raw_lead({
            "raw_lead_id": _uid(),
            "idempotency_key": _uid(),
            "pipeline_run_id": run_id,
            "company_name": "Acme",
            "contact_email": "a@b.example.com",
            "product_category": "Electronics",
            "raw_csv_row": "{}",
            "ingested_at": _now(),
        })
    for _ in range(validated):
        repo.insert_validated_lead({
            "validated_lead_id": _uid(),
            "raw_lead_id": _uid(),
            "pipeline_run_id": run_id,
            "company_name": "Acme",
            "contact_email": "a@b.example.com",
            "product_category": "Electronics",
            "validated_at": _now(),
        })
    for _ in range(invalid):
        repo.insert_validation_error({
            "error_id": _uid(),
            "pipeline_run_id": run_id,
            "error_stage": "ingestion",
            "error_message": "bad email",
            "recorded_at": _now(),
        })
    for status, count in (("success", enriched), ("validation_failed", failed)):
        for _ in range(count):
            repo.insert_enrichment({
                "enrichment_id": _uid(),
                "validated_lead_id": _uid(),
                "pipeline_run_id": run_id,
                "enrichment_status": status,
                "prompt_version": "v1.0",
                "model_name": "mock-llm-v1",
                "retry_count": 0,
            })
    for _ in range(scored):
        repo.insert_scored_lead({
            "scored_lead_id": _uid(),
            "validated_lead_id": _uid(),
            "enrichment_id": _uid(),
            "pipeline_run_id": run_id,
            "company_name": "Acme",
            "product_category": "Electronics",
            "score": 76.0,
            "score_breakdown": "{}",
            "scored_at": _now(),
        })
    session.commit()


# ---------------------------------------------------------------------------
# SQLite-backed tests — real counts + real repository persistence
# ---------------------------------------------------------------------------

def test_generate_report_returns_result_with_all_counts(session):
    _seed(session, raw=5, validated=4, invalid=1, enriched=3, failed=1, scored=3)

    result = generate_report(RUN_ID, session)

    assert isinstance(result, DataQualityReportResult)
    assert result.pipeline_run_id == RUN_ID
    assert result.total_records == 5
    assert result.valid_records == 4
    assert result.invalid_records == 1
    assert result.enriched_records == 3
    assert result.failed_enrichments == 1
    assert result.scored_records == 3


def test_generate_report_counts_raw_leads(session):
    _seed(session, raw=7, validated=7)
    result = generate_report(RUN_ID, session)
    assert result.total_records == 7


def test_generate_report_counts_validated_leads(session):
    _seed(session, raw=6, validated=6)
    result = generate_report(RUN_ID, session)
    assert result.valid_records == 6


def test_generate_report_counts_validation_errors_as_invalid(session):
    _seed(session, raw=5, validated=3, invalid=2)
    result = generate_report(RUN_ID, session)
    assert result.invalid_records == 2


def test_generate_report_counts_successful_enrichments(session):
    _seed(session, raw=4, validated=4, enriched=3, failed=1)
    result = generate_report(RUN_ID, session)
    assert result.enriched_records == 3


def test_generate_report_counts_failed_enrichments(session):
    _seed(session, raw=4, validated=4, enriched=1, failed=3)
    result = generate_report(RUN_ID, session)
    assert result.failed_enrichments == 3


def test_generate_report_counts_scored_leads(session):
    _seed(session, raw=4, validated=4, enriched=4, scored=2)
    result = generate_report(RUN_ID, session)
    assert result.scored_records == 2


def test_generate_report_inserts_row_via_repository(session):
    _seed(session, raw=3, validated=2, invalid=1, enriched=2, scored=2)

    result = generate_report(RUN_ID, session)

    stored = PipelineRepository(session).get_quality_report(RUN_ID)
    assert stored is not None
    assert stored.report_id == result.report_id
    assert stored.total_records == 3
    assert stored.valid_records == 2
    assert stored.invalid_records == 1
    assert stored.enriched_records == 2
    assert stored.scored_records == 2


def test_valid_plus_invalid_equals_total_for_consistent_data(session):
    # Consistent input: every raw lead is either validated or an error.
    _seed(session, raw=10, validated=8, invalid=2)

    result = generate_report(RUN_ID, session)

    assert result.valid_records + result.invalid_records == result.total_records


def test_enriched_plus_failed_not_greater_than_valid_for_consistent_data(session):
    _seed(session, raw=6, validated=6, enriched=4, failed=1)

    result = generate_report(RUN_ID, session)

    assert (
        result.enriched_records + result.failed_enrichments <= result.valid_records
    )


def test_zero_record_pipeline_returns_zero_counts(session):
    result = generate_report(RUN_ID, session)

    assert result.total_records == 0
    assert result.valid_records == 0
    assert result.invalid_records == 0
    assert result.enriched_records == 0
    assert result.failed_enrichments == 0
    assert result.scored_records == 0
    # A row is still written, recording the empty run.
    assert PipelineRepository(session).get_quality_report(RUN_ID) is not None


def test_counts_are_scoped_to_the_pipeline_run(session):
    _seed(session, run_id=RUN_ID, raw=3, validated=3, enriched=3, scored=3)
    _seed(session, run_id=OTHER_RUN_ID, raw=9, validated=9, enriched=9, scored=9)

    result = generate_report(RUN_ID, session)

    assert result.total_records == 3
    assert result.valid_records == 3
    assert result.enriched_records == 3
    assert result.scored_records == 3


def test_generate_report_is_deterministic(session):
    _seed(session, raw=4, validated=3, invalid=1, enriched=2, failed=1, scored=2)

    first = generate_report(RUN_ID, session)
    # Same data, a different run id → counts for that run are all zero, proving
    # the first call did not mutate the counted tables.
    second = generate_report(OTHER_RUN_ID, session)

    assert (first.total_records, first.valid_records, first.invalid_records) == (
        4,
        3,
        1,
    )
    assert (second.total_records, second.valid_records) == (0, 0)


# ---------------------------------------------------------------------------
# Fake-session tests — no database at all
# ---------------------------------------------------------------------------

class FakeSession:
    """Returns canned counts in query order; records that it was reused."""

    def __init__(self, counts: list[int]) -> None:
        self._counts = list(counts)
        self.scalar_calls = 0

    def scalar(self, stmt):  # noqa: ARG002 - statement content is irrelevant here
        value = self._counts[self.scalar_calls]
        self.scalar_calls += 1
        return value


class FakeRepository:
    """Captures the report dict handed to insert_quality_report."""

    def __init__(self, session) -> None:
        self.session = session
        self.inserted: dict | None = None

    def insert_quality_report(self, report: dict) -> str:
        self.inserted = dict(report)
        return report["report_id"]


def test_generate_report_works_with_a_fake_session():
    # Counts are consumed in the order generate_report queries them:
    # total, valid, invalid, enriched, failed, scored.
    session = FakeSession([10, 8, 2, 6, 1, 6])
    captured = {}

    def repo_factory(s):
        repo = FakeRepository(s)
        captured["repo"] = repo
        return repo

    result = generate_report(
        RUN_ID,
        session,
        repository_factory=repo_factory,
        uuid_factory=lambda: "report-xyz",
    )

    # Every count is mapped onto the result.
    assert result.total_records == 10
    assert result.valid_records == 8
    assert result.invalid_records == 2
    assert result.enriched_records == 6
    assert result.failed_enrichments == 1
    assert result.scored_records == 6
    assert result.report_id == "report-xyz"
    # Six count queries were executed against the injected session.
    assert session.scalar_calls == 6


def test_generate_report_reuses_injected_session_and_persists():
    session = FakeSession([0, 0, 0, 0, 0, 0])
    seen = {}

    def repo_factory(s):
        repo = FakeRepository(s)
        seen["repo"] = repo
        return repo

    generate_report(
        RUN_ID,
        session,
        repository_factory=repo_factory,
        uuid_factory=lambda: "report-1",
    )

    repo = seen["repo"]
    # The repository was built from the *exact* session we passed in — the
    # function never created a session of its own.
    assert repo.session is session
    # The report was persisted through the repository with all six counts.
    assert repo.inserted is not None
    for key in (
        "report_id",
        "pipeline_run_id",
        "total_records",
        "valid_records",
        "invalid_records",
        "enriched_records",
        "failed_enrichments",
        "scored_records",
        "created_at",
    ):
        assert key in repo.inserted
    assert repo.inserted["pipeline_run_id"] == RUN_ID


def test_generate_report_accepts_uuid_pipeline_run_id():
    run_uuid = uuid.UUID(RUN_ID)
    session = FakeSession([1, 1, 0, 1, 0, 1])

    result = generate_report(
        run_uuid,
        session,
        repository_factory=FakeRepository,
        uuid_factory=lambda: "report-1",
    )

    # The UUID is normalised to its string form on the result.
    assert result.pipeline_run_id == RUN_ID
