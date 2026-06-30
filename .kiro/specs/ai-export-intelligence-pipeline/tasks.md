# Implementation Plan: AI Export Intelligence Pipeline

## Overview

Build the pipeline incrementally, starting with scaffolding and infrastructure, then adding business logic one module at a time. The mock LLM path (`MOCK_LLM_ENABLED=true`) is fully implemented before any real LLM integration — this means the complete pipeline can be run, tested, and demonstrated without an API key. All data is synthetic.

**Language:** Python 3.11  
**Stack:** PostgreSQL 15, FastAPI, Pydantic v2, SQLAlchemy, Streamlit, pytest, hypothesis, structlog, Docker

---

## Tasks

- [ ] 1. Project scaffold, dependency setup, and folder structure
  - Create the full folder tree defined in design.md: `src/`, `tests/`, `migrations/`, `data/`, `dashboard/`, plus all sub-packages with `__init__.py` files
  - Create `requirements.txt` with pinned versions: `fastapi==0.111.0`, `uvicorn==0.29.0`, `sqlalchemy==2.0.30`, `psycopg2-binary==2.9.9`, `pydantic[email]==2.7.1`, `pydantic-settings==2.3.1`, `requests==2.32.3`, `structlog==24.1.0`, `hypothesis==6.100.1`, `pytest==8.2.0`, `pytest-cov==5.0.0`, `httpx==0.27.0`, `streamlit==1.35.0`, `openai==1.30.1`
  - Create empty placeholder files matching the design folder structure so imports resolve without errors
  - _Requirements: 10.1_
  - **Objective:** Runnable Python environment with all dependencies installable via `pip install -r requirements.txt`
  - **Files to create:** `requirements.txt`, `src/__init__.py`, `src/ingestion/__init__.py`, `src/validation/__init__.py`, `src/enrichment/__init__.py`, `src/scoring/__init__.py`, `src/database/__init__.py`, `src/api/__init__.py`, `src/api/routes/__init__.py`, `src/pipeline/__init__.py`, `src/knowledge_base/__init__.py`, `dashboard/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/properties/__init__.py`, `tests/smoke/__init__.py`, `tests/fixtures/__init__.py`
  - **Expected tests:** `python -c "import src"` passes; `pip install -r requirements.txt` exits 0
  - **Acceptance criteria:** All directories exist; all `__init__.py` files present; `pip install` succeeds
  - **Suggested commit:** `chore: scaffold project structure and pin dependencies`

- [x] 2. Environment config and settings module
  - Implement `src/config.py` using `pydantic_settings.BaseSettings` (from the `pydantic-settings` package, required for Pydantic v2) to read all env vars from the table in design.md
  - Include defaults: `MOCK_LLM_ENABLED=true`, `KB_ENABLED=false`, `IDEMPOTENCY_MODE=skip`, `RETRY_MAX_ATTEMPTS=3`, `RETRY_DELAY_SECONDS=2.0`, `LLM_TIMEOUT_SECONDS=30`, `LOG_LEVEL=INFO`, `HYPOTHESIS_PROFILE=dev`
  - `DATABASE_URL` is required — application raises `SystemExit` with a clear message if missing
  - Create `.env.example` with all variables documented
  - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_
  - **Files to create:** `src/config.py`, `.env.example`
  - **Expected tests:** `tests/unit/test_config.py` — test defaults loaded, missing `DATABASE_URL` raises, env var override works
  - **Acceptance criteria:** `Settings()` works with only `DATABASE_URL` set; all fields have correct types and defaults
  - **Suggested commit:** `feat(config): add pydantic settings with env var defaults and .env.example`


- [x] 3. Pydantic input and enrichment schemas
  - Implement `src/validation/input_schemas.py`: `RawLeadSchema` (company_name, contact_email, contact_phone, product_category, annual_revenue, target_market) with `EmailStr`, optional fields, and field validators
  - Implement `src/validation/enrichment_schemas.py`: `EnrichmentOutputSchema` (market_potential 0–1, export_readiness 0–1, risk_assessment dict, recommended_markets list[str], confidence_score 0–1) with range validators using `field_validator`
  - Add `RiskAssessmentSchema` as a nested model with `overall_risk: float` (0–1)
  - Export `IngestionResult`, `EnrichmentResult`, `ScoringResult` dataclasses for inter-module communication
  - _Requirements: 2.1, 2.4, 2.5, 3.2, 3.6, 9.1_
  - **Files to create:** `src/validation/input_schemas.py`, `src/validation/enrichment_schemas.py`
  - **Expected tests:** `tests/unit/test_schema_validator.py` — valid records pass, missing required fields fail, wrong types fail, out-of-range floats fail; property test P5 (determinism), P6 (field attribution), P8 (type enforcement)
  - **Acceptance criteria:** `RawLeadSchema.model_validate(valid_dict)` succeeds; invalid dicts raise `ValidationError` with field name in error message
  - **Suggested commit:** `feat(validation): add RawLeadSchema and EnrichmentOutputSchema with pydantic v2`

- [x] 4. PostgreSQL schema migration
  - Write `migrations/001_initial_schema.sql` creating all 7 tables in the correct FK order: `pipeline_runs` first, then `raw_leads`, `validated_leads`, `enrichments`, `scored_leads`, `data_quality_reports`, `validation_errors`
  - Include all columns, types, constraints, indexes defined in the design data models section
  - Add `CREATE EXTENSION IF NOT EXISTS "pgcrypto"` for `gen_random_uuid()`
  - Create `migrations/run_migrations.py` helper script that applies all `.sql` files in order using psycopg2
  - _Requirements: 6.1–6.8_
  - **Files to create:** `migrations/001_initial_schema.sql`, `migrations/run_migrations.py`
  - **Expected tests:** `tests/smoke/test_schema.py` (after Docker setup) — all tables exist with correct columns; unique constraint on `idempotency_key` works
  - **Acceptance criteria:** Migration applies cleanly on empty PostgreSQL 15; re-running is idempotent (use `CREATE TABLE IF NOT EXISTS`)
  - **Suggested commit:** `feat(db): add initial schema migration for all 7 tables`

- [x] 5. SQLAlchemy ORM models and database session
  - Implement `src/database/models.py` with SQLAlchemy 2.0 mapped classes for all 7 tables (pipeline_runs, raw_leads, validated_leads, enrichments, scored_leads, data_quality_reports, validation_errors), mirroring the SQL schema exactly
  - Implement `src/database/session.py` with `get_engine(database_url)`, `SessionLocal` factory, and `get_db()` generator for FastAPI dependency injection
  - Use `UUID` type with `server_default=text("gen_random_uuid()")` for primary keys
  - _Requirements: 6.5, 6.6, 6.7_
  - **Files to create:** `src/database/models.py`, `src/database/session.py`
  - **Expected tests:** `tests/unit/test_models.py` — ORM model attributes match expected column names and types; session factory returns a session
  - **Acceptance criteria:** `from src.database.models import RawLead, Enrichment` imports without error; `SessionLocal()` returns a SQLAlchemy session
  - **Suggested commit:** `feat(db): add sqlalchemy 2.0 orm models and session factory`

- [ ] 6. Database repository layer
  - Implement `src/database/repository.py` with `PipelineRepository` class exposing all methods defined in the design `DatabaseLayer` interface: `insert_raw_lead`, `get_raw_lead_by_idempotency_key`, `insert_validated_lead`, `insert_enrichment`, `insert_scored_lead`, `create_pipeline_run`, `update_pipeline_run`, `insert_quality_report`, `get_scored_leads`, `get_scored_lead_by_id`, `get_quality_report`, `insert_validation_error`
  - Accept SQLAlchemy `Session` as a constructor argument (not global state)
  - _Requirements: 6.1–6.8_
  - **Files to create:** `src/database/repository.py`
  - **Expected tests:** `tests/unit/test_repository.py` — use SQLite in-memory via SQLAlchemy for basic unit tests (insert/query round-trip for simple cases); NOTE: PostgreSQL-specific features (UUID defaults, JSONB, ARRAY, indexes, unique constraints) must be tested separately in integration tests with a real PostgreSQL instance
  - **Acceptance criteria:** All interface methods exist and are callable; no global session state; basic insert/query works in SQLite
  - **Suggested commit:** `feat(db): add pipeline repository with all CRUD methods`


- [x] 7. Deterministic idempotency key generation
  - Implement `src/ingestion/idempotency.py` with `generate_idempotency_key(lead_dict: dict) -> str`
  - Build the key from `company_name`, `contact_email`, `product_category` and `target_market` using deterministic SHA-256 hashing
  - Accept both `dict` and `RawLeadSchema` input
  - Normalize whitespace, casing, `None`, empty values and missing `target_market` consistently
  - Return 64-char hex string
  - _Requirements: 20.1_
  - **Files to create:** `src/ingestion/idempotency.py`
  - **Expected tests:** `tests/unit/test_idempotency.py` — same dict produces same key twice; property test P16 (determinism); whitespace/case variations normalize correctly
  - **Acceptance criteria:** `generate_idempotency_key(d1) == generate_idempotency_key(d2)` when d1 and d2 have the same company_name, contact_email, product_category and target_market after normalization
  - **Suggested commit:** `feat(ingestion): add deterministic idempotency key generator`

- [x] 8. CSV ingestion module with idempotency handling
  - Implement `src/ingestion/csv_ingestion.py` exposing `ingest_csv_file(file_path, pipeline_run_id, repository) -> IngestionResult`
  - Accept an injected `PipelineRepository`-like `repository` object; do not create database sessions
  - Parse CSV using Python `csv.DictReader`, validate row presence, handle encoding errors
  - For each row:
    1. Generate idempotency key with `generate_idempotency_key` and store it on the `raw_leads` payload
    2. Validate with `RawLeadSchema`
       - **On validation success**: insert valid rows through `insert_raw_lead` and `insert_validated_lead` (both in the same transaction for this row)
       - **On validation failure**: record invalid rows through `insert_validation_error`; do NOT insert `validated_leads`
  - Keep row-level validation failures isolated
  - Do not implement skip/update/reprocess behavior in this task; duplicate handling will be added in a later task
  - Return counts: total, inserted, skipped, failed
  - The orchestrator reads `validated_leads` filtered by `pipeline_run_id` when iterating leads for enrichment
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.3, 20.2–20.9_
  - **Files to create:** `src/ingestion/csv_ingestion.py`
  - **Expected tests:** `tests/unit/test_csv_ingestion.py` — valid CSV inserts both raw_leads and validated_leads; invalid row inserts validation_errors but not validated_leads; malformed CSV returns error message; property test P1 (record count), P2 (error messages), P3 (required fields), P4 (round-trip integrity)
  - **Acceptance criteria:** `ingest_file("data/sample/leads.csv", run_id, session)` succeeds; valid rows appear in both `raw_leads` and `validated_leads`; invalid rows appear only in `validation_errors`; duplicate rows on second run are skipped (skip mode)
  - **Suggested commit:** `feat(ingestion): add csv ingestion with idempotency, raw+validated insert, and error recording`

- [x] 9. Structlog configuration and logging setup
  - Implement `src/logging_config.py` (named `logging_config.py` to avoid shadowing the stdlib `logging` module) with `configure_logging()` that sets up structlog with console or JSON output, timestamp, level, and helpers (`get_logger`, `bind_pipeline_context` for pipeline_run_id / component)
  - Call `configure_logging()` at application entry points
  - _Requirements: 14.1, 14.4_
  - **Files to create:** `src/logging.py`
  - **Expected tests:** Manual inspection of log output format (JSON parseable)
  - **Acceptance criteria:** All log entries are valid JSON with required fields
  - **Suggested commit:** `feat(logging): add structlog json logging setup`

- [x] 10. Mock LLM provider
  - Implemented `src/enrichment/mock_llm.py` with `MockLLMProvider` exposing `enrich_lead(lead, context=None) -> EnrichmentOutputSchema` (returns a validated schema instance rather than a raw JSON string; seeded deterministically from the lead's content)
  - Return deterministic synthetic JSON seeded by `idempotency_key` using Python `random.Random(seed=idempotency_key)`
  - Output must always pass `EnrichmentOutputSchema` validation: `market_potential`, `export_readiness`, `confidence_score` all floats in [0, 1]; `risk_assessment` dict with `overall_risk`; `recommended_markets` list[str]
  - Return the JSON as a string (simulate LLM API response)
  - Hardcode `model_name = "mock-llm-v1"`
  - _Requirements: 3.2, 3.3_
  - **Files to create:** `src/enrichment/mock_llm.py`
  - **Expected tests:** `tests/unit/test_mock_llm.py` — same lead input produces same output twice (determinism); output always validates against `EnrichmentOutputSchema`; property test P18 (mock LLM determinism)
  - **Acceptance criteria:** `MockLLMProvider().generate(lead, key)` returns valid JSON string; calling twice with same key produces identical output
  - **Suggested commit:** `feat(enrichment): add deterministic mock llm provider for keyless testing`


- [x] 11. Prompt builder
  - Implement `src/enrichment/prompt_builder.py` with `build_prompt(lead: RawLeadSchema, context: Optional[str], prompt_version: str) -> str`
  - Template includes all lead fields (company_name, product_category, target_market, annual_revenue, contact_email)
  - Append optional `context` section if provided
  - Include system instruction requesting structured JSON output matching `EnrichmentOutputSchema`
  - Return multi-line formatted string
  - _Requirements: 3.1_
  - **Files to create:** `src/enrichment/prompt_builder.py`
  - **Expected tests:** `tests/unit/test_prompt_builder.py` — prompt contains all lead fields; property test P9 (prompt contains lead data)
  - **Acceptance criteria:** `build_prompt(lead, None, "v1.0")` returns string containing `lead.company_name`, `lead.product_category`, `lead.target_market`
  - **Suggested commit:** `feat(enrichment): add structured prompt builder for llm enrichment`

- [x] 12. Retry policy classifier
  - Implement `src/enrichment/retry_policy.py` with `is_retryable(enrichment_status: str) -> bool` and `should_retry(enrichment_status: str, retry_count: int, max_retries: int) -> bool`
  - Retryable: `timeout`, `network_error`, `rate_limited`
  - Non-retryable: all other statuses
  - _Requirements: 18.1, 18.2, 18.3, 18.5, 18.6, 18.7_
  - **Files to create:** `src/enrichment/retry_policy.py`
  - **Expected tests:** `tests/unit/test_retry_policy.py` — test all 9 enrichment_status values; property test P13 (status classification); P15 (retry count ceiling)
  - **Acceptance criteria:** `is_retryable("timeout")` returns `True`; `is_retryable("validation_failed")` returns `False`; `should_retry("timeout", 3, 3)` returns `False`
  - **Suggested commit:** `feat(enrichment): add retry policy classifier for failure taxonomy`

- [x] 13. LLM enrichment module with validation gate
  - Implement `src/enrichment/llm_enrichment.py` with `LLMEnrichmentModule` class and `enrich_lead(validated_lead_id: UUID, lead: RawLeadSchema, idempotency_key: str, pipeline_run_id: UUID, session: Session) -> EnrichmentResult`
  - Check `MOCK_LLM_ENABLED` config: if `true`, call `MockLLMProvider.enrich_lead()` (current API; returns a validated `EnrichmentOutputSchema`); if `false`, call the real OpenAI boundary `_call_real_llm()` (isolated and monkeypatch-ready; production wiring lands in task 26)
  - Both paths converge at the validation gate: a raw JSON string is parsed and validated, a mapping or pre-validated schema instance is re-validated → `EnrichmentOutputSchema.model_validate(...)`
  - On success: store enrichment record (status=`success`, all enrichment fields populated, `prompt_version="v1.0"`, `model_name` from provider)
  - On failure: classify `enrichment_status` (validation_failed, timeout, network_error, etc.) → store error fields (`error_type`, `error_message`, `failed_at`, `retry_count=0`, `raw_llm_response` if JSON parse failed) → check retry eligibility → return `EnrichmentResult` with retry decision
  - Use `prompt_builder.build_prompt()` to construct the prompt
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 9.2, 9.3, 9.5, 14.2, 14.6, 16.1–16.10, 17.1–17.7_
  - **Files to create:** `src/enrichment/llm_enrichment.py`
  - **Expected tests:** `tests/unit/test_llm_enrichment.py` — mock mode path stores valid enrichment; property test P10 (schema validation gate), P11 (failure recording), P13 (status classification), P14 (audit fields)
  - **Acceptance criteria:** `enrich_lead()` with `MOCK_LLM_ENABLED=true` stores enrichment record with `status=success`; invalid mock output (manually corrupted) stores `status=validation_failed` with error details
  - **Suggested commit:** `feat(enrichment): add llm enrichment module with mock/real toggle and validation gate`

- [ ]* 13.1 Write property test for LLM enrichment validation gate
  - **Property 10: Enrichment output schema validation gate**
  - **Validates: Requirements 3.3, 9.2, 9.5**
  - Generate 100 random JSON blobs (mix of valid and invalid `EnrichmentOutputSchema` structures) → pass through enrichment validation → assert only valid ones reach `enrichments` table with `status=success`
  - _Test file: `tests/properties/test_enrichment_validation_gate.py`_


- [x] 14. Enrichment retry orchestration
  - Extend `LLMEnrichmentModule` with `enrich_with_retry(validated_lead_id, lead, idempotency_key, pipeline_run_id, session)` method that wraps `enrich_lead()` in a retry loop
  - On retryable failure: increment `retry_count` in database → sleep with exponential backoff + jitter → call `enrich_lead()` again → repeat up to `RETRY_MAX_ATTEMPTS`
  - On non-retryable failure or max retries exceeded: mark permanently failed and return
  - Use `time.sleep()` with `RETRY_DELAY_SECONDS * (2 ** retry_count) + random.uniform(0, 1)` for backoff
  - _Requirements: 18.4, 18.5, 18.6, 18.7, 18.8, 18.9_
  - **Files to create/modify:** `src/enrichment/llm_enrichment.py` (add retry method)
  - **Expected tests:** `tests/unit/test_enrichment_retry.py` — simulate retryable failure (timeout) → retry count increments → eventually stops at max; simulate non-retryable (validation_failed) → no retry
  - **Acceptance criteria:** `enrich_with_retry()` for a lead that returns `timeout` 3 times increments `retry_count` to 3 and stops; `validation_failed` does not retry
  - **Suggested commit:** `feat(enrichment): add retry loop with exponential backoff`

- [x] 15. Lead scoring module
  - Implement `src/scoring/lead_scorer.py` with `LeadScorerModule` class and `score_lead(enrichment_id: UUID, enrichment: EnrichmentOutputSchema, validated_lead_id: UUID, pipeline_run_id: UUID, session: Session) -> ScoringResult`
  - Formula: `score = (market_potential * 0.4 + export_readiness * 0.4 + (1 - risk_score) * 0.2) * 100` where `risk_score = enrichment.risk_assessment.overall_risk`
  - Handle missing fields by defaulting to 0.0 for that component
  - Store in `scored_leads` table with `score`, `score_breakdown` JSONB, `scored_at`
  - Denormalize `company_name` and `product_category` into scored_leads for query performance
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_
  - **Files to create:** `src/scoring/lead_scorer.py`
  - **Expected tests:** `tests/unit/test_lead_scorer.py` — test formula with known inputs; edge cases (all zeros, all ones); property test P12 (score always in [0, 100])
  - **Acceptance criteria:** `score_lead()` returns score in range [0, 100]; score is stored in database with breakdown
  - **Suggested commit:** `feat(scoring): add lead scoring module with 0-100 formula`

- [ ]* 15.1 Write property test for lead score range
  - **Property 12: Lead score range**
  - **Validates: Requirements 5.1**
  - Use hypothesis to generate 100 random `EnrichmentOutputSchema` instances (including edge values 0.0 and 1.0) → calculate score → assert `0 <= score <= 100`
  - _Test file: `tests/properties/test_score_range.py`_

- [x] 16. Knowledge base module (stub)
  - Implement `src/knowledge_base/kb_module.py` with `KnowledgeBaseModule` class
  - Method `retrieve_context(product_category: str, target_market: str) -> Optional[str]` always returns `None` (stub implementation for now)
  - Property `is_enabled` reads `KB_ENABLED` from config
  - _Requirements: 4.1, 4.2, 4.3, 4.4_
  - **Files to create:** `src/knowledge_base/kb_module.py`
  - **Expected tests:** None (stub); integration test verifies enrichment proceeds when context is None
  - **Acceptance criteria:** `KnowledgeBaseModule().retrieve_context("electronics", "EU")` returns `None`; `is_enabled` reads config
  - **Suggested commit:** `feat(kb): add knowledge base stub returning None`

- [x] 17. Pipeline orchestrator and pipeline_run tracking
  - Implement `src/pipeline/orchestrator.py` with `PipelineOrchestrator` class and `run(file_path: Path) -> PipelineRunResult`
  - Generate `pipeline_run_id` UUID → create `pipeline_runs` record (status=`in_progress`, started_at=now)
  - Call `CSVIngestionModule.ingest_file()`
  - For each validated lead: call `LLMEnrichmentModule.enrich_with_retry()` → if success, call `LeadScorerModule.score_lead()` → continue to next lead on failure (never stop pipeline)
  - Accumulate counters: `processed_count`, `success_count`, `failed_count`
  - After all leads: update `pipeline_runs` (status=`completed`/`failed`/`partially_completed`, finished_at=now, counts)
  - Wrap each lead processing in try/except to ensure continuation
  - _Requirements: 14.3, 15.1–15.7_
  - **Files to create:** `src/pipeline/orchestrator.py`
  - **Expected tests:** `tests/integration/test_pipeline_orchestrator.py` — full run with sample CSV in `MOCK_LLM_ENABLED=true` mode; verify `pipeline_runs` record updated; verify scored_leads populated
  - **Acceptance criteria:** `orchestrator.run("data/sample/leads.csv")` creates pipeline_run, ingests, enriches, scores, updates status to `completed`
  - **Suggested commit:** `feat(pipeline): add orchestrator with full run lifecycle tracking`


- [x] 18. Data quality report generation
  - Extend `PipelineOrchestrator` to call `generate_data_quality_report(pipeline_run_id, session)` after pipeline completes
  - Implement report generation in `src/pipeline/data_quality.py` with `generate_report()` function
  - Query counts from `raw_leads`, `validated_leads`, `enrichments`, `scored_leads` for the given `pipeline_run_id`
  - Calculate: `total_records`, `valid_records`, `invalid_records`, `enriched_records` (status=success), `failed_enrichments`, `scored_records`
  - Store in `data_quality_reports` table
  - _Requirements: 21.1–21.8_
  - **Files to create:** `src/pipeline/data_quality.py`
  - **Expected tests:** `tests/unit/test_data_quality.py` — generate report from known counts → assert stored correctly; property test P17 (counts consistency: valid + invalid = total)
  - **Acceptance criteria:** `generate_report(run_id, session)` inserts a row in `data_quality_reports`; `valid_records + invalid_records == total_records`
  - **Suggested commit:** `feat(pipeline): add data quality report generation`

- [ ]* 18.1 Write property test for data quality report counts
  - **Property 17: Data quality report counts consistency**
  - **Validates: Requirements 21.2–21.7**
  - Generate random pipeline outcomes (total, valid, invalid, enriched, failed, scored) → generate report → assert `valid + invalid = total` and `enriched + failed <= valid`
  - _Test file: `tests/properties/test_quality_report_counts.py`_

- [x] 19. FastAPI application scaffold and database dependency
  - Implement `src/api/main.py` with FastAPI app instance, lifespan context manager, and `/health` endpoint
  - Add database session dependency `get_db()` from `src/database/session.py` using `Depends()`
  - Configure CORS middleware for dashboard access
  - _Requirements: 7.6_
  - **Files to create:** `src/api/main.py`
  - **Expected tests:** `tests/unit/test_api_main.py` — test `/health` endpoint returns 200
  - **Acceptance criteria:** `uvicorn src.api.main:app --reload` starts without errors; `GET /health` returns `{"status": "ok"}`
  - **Suggested commit:** `feat(api): add fastapi app scaffold with health endpoint`

- [x] 20. FastAPI leads routes
  - Implement `src/api/routes/leads.py` with 3 routes:
    - `GET /leads?min_score={float}` → list all scored leads, optional filter by min_score
    - `GET /leads/{lead_id}` → single lead by UUID (404 if not found)
    - `GET /leads/filter?min_score={float}` → explicit filter endpoint
  - Use `PipelineRepository.get_scored_leads()` and `get_scored_lead_by_id()`
  - Return `ScoredLeadResponse` Pydantic models defined in `src/api/schemas.py`
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
  - **Files to create:** `src/api/routes/leads.py`, `src/api/schemas.py` (response models)
  - **Expected tests:** `tests/integration/test_api_leads.py` — use `TestClient` with test DB; insert test data → call endpoints → assert responses; test 404 behavior
  - **Acceptance criteria:** All 3 endpoints return JSON; 404 for missing lead_id; `min_score` filter works
  - **Suggested commit:** `feat(api): add leads routes with filtering and 404 handling`

- [x] 21. FastAPI pipeline runs and quality report routes
  - Implement `src/api/routes/pipeline_runs.py` with 2 routes:
    - `GET /pipeline-runs` → list all pipeline runs with summary (use `SELECT * FROM pipeline_runs ORDER BY started_at DESC`)
    - `GET /pipeline-runs/{run_id}/report` → get data quality report for a specific run (404 if not found)
  - Use `PipelineRepository.get_quality_report()`
  - Return `PipelineRunResponse` and `DataQualityReportResponse` models
  - _Requirements: 21.9, 21.10_
  - **Files to create:** `src/api/routes/pipeline_runs.py`
  - **Expected tests:** `tests/integration/test_api_pipeline_runs.py` — insert test pipeline_run and report → call endpoints → assert responses
  - **Acceptance criteria:** Both endpoints return JSON; report endpoint returns 404 when run_id not found
  - **Suggested commit:** `feat(api): add pipeline runs and quality report routes`


- [x] 22. Synthetic sample data generation
  - Write `data/sample/generate_sample_data.py` script that generates `data/sample/leads.csv` with 20 synthetic lead records
  - Include all required fields; vary `product_category` and `target_market` values; use plausible-but-fictional company names and emails
  - Include 1 duplicate row (to test idempotency), 1 row with missing `contact_email`, 1 row with missing `product_category`
  - Commit the generated `leads.csv` file into the repository
  - _Requirements: 1.5, 11.6_
  - **Files to create:** `data/sample/generate_sample_data.py`, `data/sample/leads.csv`
  - **Expected tests:** Manual inspection: run pipeline against leads.csv → 18 valid, 2 invalid, 1 skipped duplicate
  - **Acceptance criteria:** CSV file has 20 rows + header; pipeline processes it without crashing
  - **Suggested commit:** `data: add synthetic sample leads csv with 20 records`

- [x] 23. Checkpoint — full pipeline smoke test with mock LLM
  - Run the complete pipeline against `data/sample/leads.csv` with `MOCK_LLM_ENABLED=true` and a local PostgreSQL instance
  - Verify: `pipeline_runs` has 1 row with `status=completed`; `raw_leads` has expected count; `enrichments` has `status=success` rows; `scored_leads` populated; `data_quality_reports` has 1 row
  - Add `tests/smoke/test_pipeline_smoke.py` that executes this verification automatically
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 11.2_
  - **Files to create:** `tests/smoke/test_pipeline_smoke.py`
  - **Expected tests:** All existing unit tests pass; smoke test verifies end-to-end flow
  - **Acceptance criteria:** `pytest tests/unit/ tests/properties/ -v` all green; pipeline runs without unhandled exceptions
  - **Suggested commit:** `test: add smoke test for full pipeline with mock llm`

- [x] 24. Streamlit dashboard
  - Implement `dashboard/app.py` with 4 pages using `st.sidebar` navigation:
    - **Overview**: fetch `GET /leads` → show total count, average score, score distribution histogram using `st.bar_chart`
    - **Lead List**: fetch `GET /leads?min_score={slider}` → show sortable `st.dataframe` filtered by score slider
    - **Lead Detail**: select lead from list → fetch `GET /leads/{lead_id}` → display all enrichment fields with `st.json`
    - **Pipeline Runs**: fetch `GET /pipeline-runs` → show `data_quality_reports` table with `st.dataframe`
  - Use `requests` library to call FastAPI (base URL from `API_BASE_URL` env var)
  - Handle API errors gracefully (show warning message when API is unreachable)
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 21.10_
  - **Files to create:** `dashboard/app.py`
  - **Expected tests:** Manual: run `streamlit run dashboard/app.py` → verify all 4 pages load without error
  - **Acceptance criteria:** All 4 pages load; score filter updates lead list dynamically; API error shows user-friendly message
  - **Suggested commit:** `feat(dashboard): add streamlit dashboard with 4 pages`

- [x] 25. Dockerfile and Docker Compose setup
  - Create `Dockerfile` for the Python app container: `python:3.11-slim` base, install requirements.txt, copy `src/`, `migrations/`, `data/` directories, expose port 8000, run migrations then start uvicorn
  - Create `Dockerfile.dashboard` for the Streamlit container: install dashboard requirements, expose port 8501, `CMD ["streamlit", "run", "dashboard/app.py"]`
  - Create `docker-compose.yml` matching the design spec: `app` (port 8000), `db` (postgres:15, pgdata volume), `dashboard` (port 8501, depends_on app)
  - Create `docker-compose.test.yml` for integration tests: same db service but no app/dashboard; test container runs pytest
  - All env vars wired via `environment:` in compose (DATABASE_URL, MOCK_LLM_ENABLED=true, etc.)
  - _Requirements: 10.1–10.5_
  - **Files to create:** `Dockerfile`, `Dockerfile.dashboard`, `docker-compose.yml`, `docker-compose.test.yml`
  - **Expected tests:** `docker-compose up --build` starts all services; `docker-compose ps` shows 3 containers running
  - **Acceptance criteria:** `docker-compose up -d` → all containers healthy; `GET http://localhost:8000/health` → 200; `http://localhost:8501` → dashboard loads
  - **Suggested commit:** `feat(docker): add dockerfile and docker-compose for app, db, and dashboard`


- [x] 26. Real LLM integration (optional, late-stage)
  - Extend `LLMEnrichmentModule` to add `RealLLMProvider` class using `openai.OpenAI()` client
  - Method `generate(lead: RawLeadSchema, prompt: str) -> str` calls `client.chat.completions.create()` with structured output (use `response_format={"type": "json_object"}`)
  - Read `OPENAI_API_KEY` and `OPENAI_MODEL` from config
  - Catch `openai.Timeout` → set `enrichment_status=timeout`; `openai.APIConnectionError` → `network_error`; `openai.RateLimitError` → `rate_limited`
  - Update `enrich_lead()` to select provider based on `MOCK_LLM_ENABLED` flag
  - Set `model_name` from actual API response (e.g. `"gpt-4o-mini"`)
  - _Requirements: 3.1, 3.2, 3.3, 16.3, 16.4, 16.5, 19.3_
  - **Files to create/modify:** `src/enrichment/real_llm.py`, update `llm_enrichment.py` to use `RealLLMProvider` when `MOCK_LLM_ENABLED=false`
  - **Expected tests:** `tests/integration/test_real_llm.py` (requires `OPENAI_API_KEY` env var, tagged with `@pytest.mark.live_llm`, skipped by default) — calls real API → stores enrichment with real model_name
  - **Acceptance criteria:** With `MOCK_LLM_ENABLED=false` and valid API key, `enrich_lead()` calls real API; enrichment record has `model_name="gpt-4o-mini"` (or actual model used)
  - **Suggested commit:** `feat(enrichment): add real llm provider with openai sdk`

- [x] 27. Integration test suite
  - Write comprehensive integration tests in `tests/integration/`:
    - `test_csv_ingestion_integration.py` — full ingestion flow with idempotency modes (skip, update, reprocess)
    - `test_enrichment_pipeline_integration.py` — full pipeline with mock LLM, verify all tables populated
    - `test_pipeline_run_tracking_integration.py` — verify `pipeline_runs` lifecycle and counts
    - `test_api_endpoints_integration.py` — all 6 FastAPI routes
    - `test_data_quality_report_integration.py` — verify report generation and retrieval
  - Use `docker-compose.test.yml` to spin up PostgreSQL; each test creates a fresh session and cleans up
  - _Requirements: 11.2_
  - **Files to create:** 5 integration test files
  - **Expected tests:** `docker-compose -f docker-compose.test.yml up -d && pytest tests/integration/ -v` → all pass
  - **Acceptance criteria:** All integration tests pass; no dangling transactions or test data
  - **Suggested commit:** `test: add comprehensive integration test suite`

- [x] 28. Property-based test suite
  - Write remaining property tests in `tests/properties/`:
    - `test_csv_record_count.py` — Property 1: CSV record count preservation
    - `test_required_field_enforcement.py` — Property 3: Required field enforcement
    - `test_validation_error_field_attribution.py` — Property 6: Error field attribution
    - `test_enrichment_status_taxonomy.py` — Property 13: Enrichment status classification
    - `test_retry_count_ceiling.py` — Property 15: Retry count never exceeds max
    - `test_idempotency_key_determinism.py` — Property 16: Idempotency key determinism
  - Use `hypothesis` with `max_examples=100` and strategies matching design spec
  - _Requirements: 11.1, 11.4_
  - **Files to create:** 6 property test files
  - **Expected tests:** `pytest tests/properties/ -v --hypothesis-show-statistics` → all pass, 100 examples each
  - **Acceptance criteria:** All properties pass with 100 iterations; no hypothesis health check failures
  - **Suggested commit:** `test: add property-based tests with hypothesis`

- [x] 29. README, architecture notes, and demo instructions
  - Write comprehensive `README.md` with sections: Project Purpose, Tech Stack, Architecture Overview, Setup Instructions, Running the Pipeline, Running Tests, API Documentation, Sample Output, Technology Choices, Future Enhancements
  - Include architecture diagrams (copy from design.md)
  - Add "Getting Started in 3 Minutes" section: `docker-compose up -d → docker exec -it app python -m src.pipeline.run_pipeline data/sample/leads.csv → open http://localhost:8501`
  - Create `docs/ARCHITECTURE.md` with detailed component interaction diagrams
  - Add `docs/DEMO.md` with step-by-step demo script: "Run pipeline → show logs → query API → view dashboard → explain idempotency → explain mock vs real LLM"
  - _Requirements: 12.1–12.7_
  - **Files to create:** `README.md`, `docs/ARCHITECTURE.md`, `docs/DEMO.md`
  - **Expected tests:** Manual: follow README instructions from scratch on a clean machine
  - **Acceptance criteria:** README is complete, clear, and accurate; all links work; demo script executes without errors
  - **Suggested commit:** `docs: add comprehensive readme, architecture guide, and demo script`


- [ ] 30. Final checkpoint — ensure all tests pass and complete demo
  - Run full test suite: `pytest --cov=src --cov-report=html -v`
  - Run smoke tests against Docker Compose stack: `docker-compose up -d && pytest tests/smoke/ -v`
  - Execute demo script from `docs/DEMO.md` end-to-end
  - Verify code coverage is >80%
  - Fix any failing tests or missing coverage
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 11.5_
  - **Acceptance criteria:** All tests green; coverage report generated; demo runs without errors; all requirements validated
  - **Suggested commit:** `test: verify full test suite and demo script pass`

---

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP (primarily test-related sub-tasks)
- Each task references specific requirements for traceability
- Checkpoints (tasks 23, 30) ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests and integration tests validate specific examples and end-to-end flows
- The task list is ordered to build incrementally: infrastructure → data layer → business logic → API → dashboard → testing → documentation
- Mock LLM implementation (task 10) comes early so the full pipeline is runnable without an API key
- Real LLM integration (task 26) is late-stage and optional
- All idempotency modes (skip, update, reprocess) are implemented in task 8
- Retry policy is implemented separately from enrichment logic for testability (tasks 12, 14)
- Data quality reporting (task 18) is integrated into the orchestrator automatically
- Docker setup (task 25) happens after all core modules are complete
- Documentation (task 29) is written last when the system is fully functional

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2", "3", "4"] },
    { "id": 2, "tasks": ["5", "7", "9"] },
    { "id": 3, "tasks": ["6", "8", "10", "11", "12", "16"] },
    { "id": 4, "tasks": ["13", "13.1", "15", "15.1"] },
    { "id": 5, "tasks": ["14", "17"] },
    { "id": 6, "tasks": ["18", "18.1", "19", "22"] },
    { "id": 7, "tasks": ["20", "21", "23"] },
    { "id": 8, "tasks": ["24", "25"] },
    { "id": 9, "tasks": ["26", "27", "28"] },
    { "id": 10, "tasks": ["29"] },
    { "id": 11, "tasks": ["30"] }
  ]
}
```
