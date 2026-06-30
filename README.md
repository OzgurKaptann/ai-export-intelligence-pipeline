# AI Export Intelligence Pipeline

**Spec-driven AI/data pipeline for validating, enriching and scoring export leads.**

This project is being developed as a production-oriented data pipeline, not just a simple AI demo.  
It focuses on clean architecture, database-first design, validation, testing and step-by-step implementation.

> **Current status:** Foundation layer, CSV ingestion, structured logging, the deterministic mock LLM provider, the enrichment prompt builder, the retry policy classifier, the LLM enrichment module with validation gate, the enrichment retry orchestration loop, the lead scoring module, the knowledge base stub, the pipeline orchestrator (with `pipeline_run` lifecycle tracking), data quality report generation, the FastAPI application scaffold (`/health` endpoint, `get_db` dependency, local CORS), the FastAPI leads routes (scored lead list / detail / filter) and the FastAPI pipeline run and quality report routes (`/pipeline-runs` list, `/pipeline-runs/{run_id}/report`) completed.  
> Idempotency key generation, CSV ingestion, structured logging, the mock LLM provider, the prompt builder, the retry policy classifier, the mock-mode LLM enrichment module (with `EnrichmentOutputSchema` validation gate), the retry orchestration loop (exponential backoff with jitter over the retryable failure taxonomy), the lead scoring module (0–100 weighted score with breakdown storage), the knowledge base stub (`retrieve_context` returns `None`; `is_enabled` reads `KB_ENABLED`), the unit-testable pipeline orchestrator (`PipelineOrchestrator.run`; creates the `pipeline_runs` record, ingests, enriches each validated lead, scores successes and updates the run to `completed`/`failed`), unit-testable data quality report generation (`generate_report`; counts each stage's rows for a run and stores one `data_quality_reports` row), the FastAPI application scaffold (`src.api.main:app` with an async `lifespan`, a deterministic `GET /health` returning `{"status": "ok"}`, the re-exported `get_db` session dependency and local-development CORS — no settings load, session or database connection at import time), the FastAPI leads routes (`GET /leads` with optional `min_score`, `GET /leads/filter`, `GET /leads/{lead_id}` with 404 handling, returning `ScoredLeadResponse` models) and the FastAPI pipeline run and quality report routes (`GET /pipeline-runs` listing runs newest-first as `PipelineRunResponse`, `GET /pipeline-runs/{run_id}/report` returning the run's `DataQualityReportResponse` with a 404 when missing) and the synthetic sample data generator (`data/sample/generate_sample_data.py` plus the committed `data/sample/leads.csv` with 20 fictional records) are implemented; real knowledge base retrieval (RAG / vector search), real OpenAI enrichment (boundary only so far), the Streamlit dashboard, Docker, and the full PostgreSQL/Docker integration and smoke tests (Task 23) are planned for upcoming iterations.

---

## Repository Description

Spec-driven AI export intelligence pipeline built with Python, PostgreSQL, SQLAlchemy, Pydantic and Kiro. Currently includes validation schemas, database migrations, ORM models, repository layer, CSV ingestion, mock-mode LLM enrichment with a validation gate, lead scoring, the pipeline orchestrator with `pipeline_run` tracking, data quality report generation and test coverage; real OpenAI enrichment, API and dashboard features are planned.

---

## Project Purpose

Export teams often work with scattered lead data coming from CSV files, spreadsheets, CRM exports or manual lists.

This project aims to create a structured AI/data pipeline that can:

- ingest raw export lead data,
- validate each lead record,
- prevent duplicate processing with idempotency,
- enrich leads using an LLM or mock LLM provider,
- score leads based on export potential,
- store each pipeline stage in PostgreSQL,
- expose results through an API,
- and visualize insights in a dashboard.

The goal is to demonstrate a maintainable data pipeline with clear boundaries between validation, storage, enrichment, scoring and reporting.

---

## Current Implementation Status

Completed so far:

- Project scaffold and dependency pinning
- Environment-based configuration with `pydantic-settings`
- Pydantic validation schemas for raw leads and enrichment outputs
- PostgreSQL migration for 7 core tables
- SQLAlchemy 2.0 ORM models
- Database session factory
- Repository layer
- Deterministic idempotency key generation
- CSV ingestion module (validates rows with `RawLeadSchema`, generates idempotency keys, delegates persistence to the repository layer)
- Structured logging setup with `structlog` (`configure_logging`, `get_logger`, `bind_pipeline_context`; console or JSON output)
- Deterministic mock LLM provider (`MockLLMProvider.enrich_lead`; schema-valid synthetic enrichment, no API key, no network, no database)
- Enrichment prompt builder (`build_enrichment_prompt`; deterministic, offline prompt text including lead fields and the `EnrichmentOutputSchema` JSON output contract)
- Retry policy classifier (`is_retryable`, `should_retry`; pure, deterministic classification of the 9-value enrichment failure taxonomy — `timeout`, `network_error` and `rate_limited` are retryable, all others are not)
- LLM enrichment module with validation gate (`LLMEnrichmentModule.enrich_lead`; selects the mock provider when `MOCK_LLM_ENABLED=true`, validates every output with `EnrichmentOutputSchema`, persists success or failure metadata through the injected repository, and maps failures onto the enrichment status taxonomy — `success`, `validation_failed`, `empty_response`, `invalid_json`, `unknown_error`; the real OpenAI call is an isolated, monkeypatch-ready boundary, not yet production-wired)
- Enrichment retry orchestration (`LLMEnrichmentModule.enrich_with_retry`; wraps `enrich_lead` in a retry loop that retries only the transient statuses — `timeout`, `network_error`, `rate_limited` — using the shared retry policy, waits `RETRY_DELAY_SECONDS * (2 ** retry_count) + jitter` between attempts, stops at `RETRY_MAX_ATTEMPTS`, reuses the injected session and never opens its own; sleep/backoff are injectable for keyless, delay-free testing)
- Lead scoring module (`LeadScorerModule.score_lead`; computes a 0–100 score with the weighted formula `(market_potential * 0.4 + export_readiness * 0.4 + (1 - overall_risk) * 0.2) * 100`, defaults missing or invalid components to 0.0, clamps the result to `[0, 100]`, stores `score` plus a `score_breakdown` JSONB through the injected repository, reuses the injected session and never opens its own — no API key, no network)
- Knowledge base module stub (`KnowledgeBaseModule`; `retrieve_context(product_category, target_market)` always returns `None`, `is_enabled` reads `KB_ENABLED` lazily through an injectable settings provider — no import-time side effects, no embeddings, no vector store, no real retrieval yet)
- Pipeline orchestrator with `pipeline_run` tracking (`PipelineOrchestrator.run`; generates the `pipeline_run_id`, creates the `pipeline_runs` record with `status="in_progress"` and `started_at`, calls CSV ingestion, then for each validated lead calls `enrich_with_retry` and — only on success — `score_lead`, isolating per-lead failures so one bad lead never stops the run, generates the run's data quality report, and updates the run to `completed`/`failed` with `finished_at` and counts; fully dependency-injected — `session_factory`, `repository_factory`, ingestion function, enrichment/scorer modules, the data quality report function, `uuid_factory` and `clock` are all injectable — opens exactly one session per run and reuses it for every lead, with no import-time settings load or session creation, no API key and no network)
- Data quality report generation (`src/pipeline/data_quality.generate_report`; counts `raw_leads` (total), `validated_leads` (valid), `validation_errors` (invalid), successful vs failed `enrichments`, and `scored_leads` for a given `pipeline_run_id`, then stores one `data_quality_reports` row through the injected repository; reuses the orchestrator's single session and never opens its own, and report generation is best-effort so a reporting failure never demotes an otherwise completed run — no API key, no network)
- FastAPI application scaffold (`src/api/main.py`; importable as `src.api.main:app`, async `lifespan` context manager, deterministic `GET /health` returning `{"status": "ok"}`, the `get_db` database session dependency re-exported from `src/database/session.py` for `Depends()`, and local-development CORS for the Streamlit/front-end dashboard origins — no settings load, no session and no database connection at import time)
- FastAPI leads routes (`src/api/routes/leads.py`; `GET /leads` lists scored leads with an optional `min_score` filter, `GET /leads/filter` is the explicit filter endpoint and `GET /leads/{lead_id}` returns a single scored lead by UUID with a 404 when missing — all backed by `PipelineRepository.get_scored_leads()` / `get_scored_lead_by_id()` and serialised as `ScoredLeadResponse` Pydantic models from `src/api/schemas.py`; the repository is provided through an overridable `get_repository` dependency, `/leads/filter` is declared before `/leads/{lead_id}` so it is never parsed as a UUID, and the routes need no `DATABASE_URL` or `OPENAI_API_KEY` when the repository dependency is overridden in tests)
- FastAPI pipeline run and quality report routes (`src/api/routes/pipeline_runs.py`; `GET /pipeline-runs` lists every run newest-first via `SELECT * FROM pipeline_runs ORDER BY started_at DESC` as `PipelineRunResponse` models, and `GET /pipeline-runs/{run_id}/report` returns the run's data quality report as a `DataQualityReportResponse` — backed by `PipelineRepository.get_quality_report()` — with a 404 when no report exists and a 422 for a malformed UUID; the request session and repository are provided through overridable `get_session` / `get_repository` dependencies, so the routes need no `DATABASE_URL` or `OPENAI_API_KEY` when those dependencies are overridden in tests)
- Unit tests for configuration, schemas, ORM models, repository behavior, CSV ingestion, logging setup, the mock LLM provider, the prompt builder, the retry policy classifier, the LLM enrichment module, the enrichment retry orchestration, the lead scoring module, the knowledge base stub, the pipeline orchestrator, data quality report generation, the FastAPI scaffold (`/health`, CORS, keyless/DB-less import), the FastAPI leads routes (list / filter / detail, `min_score` filtering, 404 and invalid-UUID handling, route ordering, keyless/DB-less via dependency override) and the FastAPI pipeline run and quality report routes (run list shape and `started_at` descending ordering, report retrieval, 404 and invalid-UUID handling, keyless/DB-less via dependency override)
- Synthetic sample data generator (`data/sample/generate_sample_data.py`; deterministic, standard-library-only script — no random values, no network, no database, no API key — that writes `data/sample/leads.csv` with 20 fictional export lead records plus a header, using `.example` email domains and varied `product_category` / `target_market` values; includes 18 schema-valid rows, one exact business-identity duplicate to exercise idempotency, one row missing `contact_email` and one row missing `product_category`)
- **408 passing unit tests**

Planned next:

- Real knowledge base retrieval (RAG / vector search)
- Real OpenAI enrichment integration (production wiring)
- Streamlit dashboard
- Docker Compose setup
- PostgreSQL integration tests
- End-to-end smoke tests (full PostgreSQL pipeline smoke test — Task 23)

---

## High-Level Architecture

```mermaid
flowchart TD
    A[CSV Export Lead File] --> B[CSV Ingestion Module]
    B --> C[Raw Lead Validation]
    C --> D{Valid Record?}

    D -- No --> E[validation_errors]
    D -- Yes --> F[Idempotency Resolution]

    F --> G[raw_leads]
    G --> H[validated_leads]

    H --> I[Optional Knowledge Base Context]
    I --> J[LLM / Mock LLM Enrichment]
    J --> K[Enrichment Schema Validation]

    K -- Failed --> L[Enrichment Failure Tracking]
    K -- Success --> M[enrichments]

    M --> N[Lead Scoring Module]
    N --> O[scored_leads]

    O --> P[FastAPI API]
    O --> Q[Streamlit Dashboard]

    B --> R[pipeline_runs]
    E --> S[data_quality_reports]
    L --> S
    O --> S
```

---

## Pipeline Flow

```mermaid
sequenceDiagram
    participant CSV as CSV File
    participant ING as Ingestion Module
    participant VAL as Validator
    participant DB as PostgreSQL
    participant LLM as LLM / Mock LLM
    participant SCORE as Scoring Module
    participant API as FastAPI / Dashboard

    CSV->>ING: Read lead rows
    ING->>VAL: Validate required fields and types
    VAL-->>DB: Store invalid rows as validation_errors
    VAL-->>DB: Store valid rows as raw_leads and validated_leads
    DB->>LLM: Send validated lead for enrichment
    LLM-->>DB: Store enrichment output or failure metadata
    DB->>SCORE: Score successfully enriched leads
    SCORE-->>DB: Store scored_leads
    DB-->>DB: Generate data_quality_report
    DB->>API: Serve scored leads and reports
```

---

## CSV Ingestion

The CSV ingestion module (`src/ingestion/csv_ingestion.py`) is the entry point of the pipeline's data layer. Its single public function:

```python
ingest_csv_file(file_path, pipeline_run_id, repository) -> IngestionResult
```

reads each row from a UTF-8 CSV file with `csv.DictReader` and processes it as follows:

- **Required columns:** `company_name`, `contact_email`, `product_category`.
- **Optional columns:** `contact_phone`, `annual_revenue`, `target_market`.
- Each row is validated with `RawLeadSchema`.
- **Valid rows** are written to `raw_leads` **and** `validated_leads` in the same logical step, after a deterministic `idempotency_key` is generated and the original CSV row is preserved in `raw_csv_row`.
- **Invalid rows** are recorded in `validation_errors` (with the offending field and message) and never reach `raw_leads` or `validated_leads`.
- Row-level validation failures are isolated, so one malformed row does not stop the rest of the file; file-level errors (such as a missing file) are not swallowed.

The module is pure application logic around CSV parsing, schema validation and idempotency key generation. It never creates a database session and delegates all persistence to an injected `PipelineRepository`. The returned `IngestionResult` reports the `total`, `inserted` and `failed` counts for the run.

> Duplicate-handling modes (`skip` / `update` / `reprocess`) are not implemented yet — ingestion currently generates and stores the idempotency key only.

---

## Database Design

The PostgreSQL schema currently contains 7 core tables.

```mermaid
erDiagram
    pipeline_runs ||--o{ raw_leads : contains
    pipeline_runs ||--o{ validated_leads : tracks
    pipeline_runs ||--o{ enrichments : tracks
    pipeline_runs ||--o{ scored_leads : tracks
    pipeline_runs ||--|| data_quality_reports : summarizes
    pipeline_runs ||--o{ validation_errors : logs

    raw_leads ||--o{ validated_leads : validates
    raw_leads ||--o{ validation_errors : may_have
    validated_leads ||--o{ enrichments : enriches
    validated_leads ||--o{ scored_leads : scores
    enrichments ||--o{ scored_leads : produces

    pipeline_runs {
        uuid pipeline_run_id PK
        timestamptz started_at
        timestamptz finished_at
        text status
        integer processed_count
        integer success_count
        integer failed_count
        text file_path
        jsonb run_metadata
    }

    raw_leads {
        uuid raw_lead_id PK
        text idempotency_key UK
        uuid pipeline_run_id FK
        text company_name
        text contact_email
        text product_category
        jsonb raw_csv_row
        timestamptz ingested_at
    }

    validated_leads {
        uuid validated_lead_id PK
        uuid raw_lead_id FK
        uuid pipeline_run_id FK
        text company_name
        text contact_email
        text product_category
        timestamptz validated_at
    }

    enrichments {
        uuid enrichment_id PK
        uuid validated_lead_id FK
        uuid pipeline_run_id FK
        text enrichment_status
        numeric market_potential
        numeric export_readiness
        jsonb risk_assessment
        text_array recommended_markets
        numeric confidence_score
        integer retry_count
    }

    scored_leads {
        uuid scored_lead_id PK
        uuid validated_lead_id FK
        uuid enrichment_id FK
        uuid pipeline_run_id FK
        numeric score
        jsonb score_breakdown
        timestamptz scored_at
    }

    data_quality_reports {
        uuid report_id PK
        uuid pipeline_run_id FK
        integer total_records
        integer valid_records
        integer invalid_records
        integer enriched_records
        integer failed_enrichments
        integer scored_records
    }

    validation_errors {
        uuid error_id PK
        uuid pipeline_run_id FK
        uuid raw_lead_id FK
        text error_stage
        text error_field
        text error_message
        timestamptz recorded_at
    }
```

---

## Core Tables

| Table | Purpose |
|---|---|
| `pipeline_runs` | Tracks each pipeline execution |
| `raw_leads` | Stores deduplicated raw lead records |
| `validated_leads` | Stores schema-valid lead records |
| `enrichments` | Stores LLM/mock LLM enrichment outputs and failure metadata |
| `scored_leads` | Stores final scored leads |
| `data_quality_reports` | Stores run-level quality metrics |
| `validation_errors` | Stores validation failures |

---

## Development Workflow

```mermaid
flowchart LR
    A[Requirements] --> B[Design]
    B --> C[Tasks]
    C --> D[Implementation]
    D --> E[Tests]
    E --> F[Commit]
    F --> G[Next Task]
```

Each feature is implemented in small, reviewable commits.

Current completed commits include:

- project scaffold,
- configuration layer,
- validation schemas,
- PostgreSQL migration,
- SQLAlchemy ORM models,
- repository layer.

---

## Technology Stack

| Area | Technology |
|---|---|
| Language | Python |
| Validation | Pydantic v2 |
| Settings | pydantic-settings |
| Database | PostgreSQL 15 |
| ORM | SQLAlchemy 2.0 |
| Migration | Raw SQL migration scripts |
| Testing | pytest |
| Property Testing | Hypothesis |
| API | FastAPI (`/health` + scored lead routes + pipeline run / quality report routes) |
| Dashboard | Streamlit planned |
| AI Enrichment | OpenAI / Mock LLM planned |
| Containerization | Docker Compose planned |

---

## Repository Structure

```text
ai-export-intelligence-pipeline/
├── .kiro/
│   └── specs/
│       └── ai-export-intelligence-pipeline/
│           ├── requirements.md
│           ├── design.md
│           └── tasks.md
├── migrations/
│   ├── 001_initial_schema.sql
│   └── run_migrations.py
├── src/
│   ├── config.py
│   ├── database/
│   │   ├── models.py
│   │   ├── repository.py
│   │   └── session.py
│   ├── validation/
│   │   ├── input_schemas.py
│   │   └── enrichment_schemas.py
│   ├── ingestion/
│   ├── enrichment/
│   ├── scoring/
│   ├── pipeline/
│   ├── api/
│   └── knowledge_base/
├── tests/
│   └── unit/
├── dashboard/
├── data/
├── docs/
├── requirements.txt
├── .env.example
└── README.md
```

---

## Current Test Status

Current unit test coverage includes:

- configuration validation,
- required environment settings,
- Pydantic schema behavior,
- enrichment output validation,
- SQLAlchemy model metadata,
- database session factory,
- repository method behavior,
- CSV ingestion behavior,
- structured logging setup,
- mock LLM provider behavior,
- enrichment prompt builder behavior,
- retry policy classifier behavior,
- LLM enrichment module behavior (validation gate and failure taxonomy mapping),
- enrichment retry orchestration behavior (retryable vs non-retryable handling, retry-count ceiling, exponential backoff with jitter, injected session reuse),
- lead scoring behavior (formula correctness, all-zeros/all-ones edge cases, score bounds, missing/invalid component defaulting, breakdown storage, injected session reuse, no input mutation),
- knowledge base stub behavior (`retrieve_context` always returns `None`, `is_enabled` reads `KB_ENABLED` lazily through an injectable settings provider, no import-time side effects, no input mutation),
- pipeline orchestrator behavior (run lifecycle from `in_progress` to `completed`/`failed`, ingestion called with the generated `pipeline_run_id`, enrichment of every validated lead, scoring of only successfully enriched leads, per-lead enrichment/scoring failures not stopping the run, single injected session reused across all leads, data quality report generated after completion and skipped on pipeline-level failure, no `OPENAI_API_KEY` and no external API),
- data quality report generation behavior (per-stage row counts scoped to a `pipeline_run_id`, `valid_records + invalid_records == total_records` for consistent data, zero-record runs returning zero counts, persistence through the repository, injected-session reuse with no internally created session, no external API),
- FastAPI scaffold behavior (`app` imports as a `FastAPI` instance, `GET /health` returns `200` with `{"status": "ok"}`, `/health` needs no `DATABASE_URL` or `OPENAI_API_KEY`, CORS middleware is registered and answers local dashboard origins, the `get_db` dependency forwards `src.database.session.get_db`, importing the app opens no database connection, the `/leads` routes are registered while no `/pipeline-runs` routes are),
- FastAPI leads route behavior (`GET /leads` returns a JSON list of `ScoredLeadResponse`-shaped records and forwards `min_score` to the repository, `GET /leads/filter` is resolved as the filter endpoint rather than a `{lead_id}` UUID, `GET /leads/{lead_id}` returns a single lead or `404` when missing and `422` for a malformed UUID, and every endpoint works with no `DATABASE_URL` or `OPENAI_API_KEY` via an overridden repository dependency),
- FastAPI pipeline run and quality report route behavior (`GET /pipeline-runs` returns a JSON list of `PipelineRunResponse`-shaped records ordered `started_at` descending, `GET /pipeline-runs/{run_id}/report` returns the run's `DataQualityReportResponse` and calls `get_quality_report(run_id)`, returns `404` when no report exists and `422` for a malformed UUID, the existing `/health` and `/leads` routes stay registered, and every endpoint works with no `DATABASE_URL` or `OPENAI_API_KEY` via overridden session/repository dependencies).

Latest local result:

```text
408 passed
```

---

## How to Run Tests

Install dependencies:

```bash
pip install -r requirements.txt
```

Run all unit tests:

```bash
python -m pytest tests/unit/ -v
```

Run a specific test file:

```bash
python -m pytest tests/unit/test_repository.py -v
```

---

## Environment Variables

Use `.env.example` as the reference configuration file.

Example:

```text
DATABASE_URL=postgresql://user:password@localhost:5432/ai_export_pipeline
MOCK_LLM_ENABLED=true
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
IDEMPOTENCY_MODE=skip
RETRY_MAX_ATTEMPTS=3
LOG_LEVEL=INFO
```

---

## Planned Features

### Data Pipeline

- CSV ingestion — **implemented**
- Row-level validation — **implemented**
- Idempotency key generation — **implemented**
- Duplicate handling modes: `skip`, `update`, `reprocess`
- Pipeline orchestrator — **implemented**
- Pipeline run tracking — **implemented**
- Data quality reporting — **implemented**

### AI Enrichment

- Deterministic mock LLM mode — **implemented**
- Enrichment prompt builder — **implemented**
- Retry policy classifier — **implemented**
- Enrichment failure taxonomy classification — **implemented**
- LLM enrichment module with validation gate (mock mode) — **implemented**
- Structured JSON response validation — **implemented**
- Real LLM integration (production wiring; boundary in place)
- Retry handling (orchestration loop) — **implemented**
- Prompt and model traceability — **implemented**

### Lead Scoring

- Rule-based lead scoring — **implemented**
- Score breakdown storage — **implemented**
- Export-readiness scoring — **implemented**
- Market-potential scoring — **implemented**
- Risk-adjusted ranking — **implemented**

### Knowledge Base

- Knowledge base stub (`retrieve_context` returns `None`, `is_enabled` reads `KB_ENABLED`) — **implemented**
- Real knowledge base retrieval / context retrieval (RAG, vector search)

### API and Dashboard

- FastAPI application scaffold (`/health` endpoint, `get_db` dependency, local CORS) — **implemented**
- FastAPI leads routes (scored lead list / detail / filter with `min_score`, 404 handling) — **implemented**
- FastAPI pipeline run and quality report endpoints (`/pipeline-runs` list, `/pipeline-runs/{run_id}/report` with 404 handling) — **implemented**
- Streamlit dashboard
- Lead ranking views
- Quality metrics visualization

### Production Readiness

- Structured logs — **implemented**
- Synthetic sample data (generator script + `data/sample/leads.csv`) — **implemented**
- Docker Compose
- PostgreSQL integration tests
- Smoke tests (full PostgreSQL pipeline smoke test — planned, Task 23)
- README demo flow

---

## Turkish Summary / Türkçe Özet

Bu proje, ihracat potansiyeli olan firma ve lead verilerini uçtan uca işlemek için tasarlanan AI destekli bir veri pipeline çalışmasıdır.

Amaç; CSV gibi ham veri kaynaklarından gelen lead kayıtlarını doğrulamak, tekrar eden kayıtları idempotency mantığıyla yönetmek, LLM veya mock LLM ile zenginleştirmek, ihracat potansiyeline göre skorlamak ve sonuçları PostgreSQL üzerinde izlenebilir şekilde saklamaktır.

Şu ana kadar tamamlanan bölümler:

- proje iskeleti,
- konfigürasyon yönetimi,
- Pydantic validasyon şemaları,
- PostgreSQL migration yapısı,
- SQLAlchemy ORM modelleri,
- repository katmanı,
- idempotency anahtarı üretimi,
- CSV ingestion modülü,
- yapılandırılmış loglama (`structlog`),
- deterministik mock LLM sağlayıcısı,
- enrichment prompt builder (`build_enrichment_prompt`; deterministik, çevrimdışı prompt metni; lead alanları ve `EnrichmentOutputSchema` JSON çıktı sözleşmesi dahil),
- retry policy sınıflandırıcısı (`is_retryable`, `should_retry`; saf ve deterministik; 9 değerli enrichment hata taksonomisini sınıflandırır — `timeout`, `network_error` ve `rate_limited` yeniden denenebilir, diğerleri denenmez),
- doğrulama kapılı LLM enrichment modülü (`LLMEnrichmentModule.enrich_lead`; `MOCK_LLM_ENABLED=true` iken mock sağlayıcıyı seçer, her çıktıyı `EnrichmentOutputSchema` ile doğrular, başarı veya hata bilgisini enjekte edilen repository üzerinden saklar ve hataları enrichment durum taksonomisine eşler — `success`, `validation_failed`, `empty_response`, `invalid_json`, `unknown_error`; gerçek OpenAI çağrısı ise izole ve monkeypatch ile test edilebilir bir sınır olup henüz üretim için bağlanmamıştır),
- enrichment retry orkestrasyonu (`LLMEnrichmentModule.enrich_with_retry`; `enrich_lead` çağrısını bir retry döngüsüne sarar, yalnızca geçici hataları yeniden dener — `timeout`, `network_error`, `rate_limited` — paylaşılan retry policy’yi kullanır, denemeler arasında `RETRY_DELAY_SECONDS * (2 ** retry_count) + jitter` kadar bekler, `RETRY_MAX_ATTEMPTS` sınırında durur, enjekte edilen session’ı yeniden kullanır ve kendi session’ını açmaz; sleep/backoff davranışı anahtar gerektirmeyen ve gecikmesiz testler için enjekte edilebilir),
- lead scoring modülü (`LeadScorerModule.score_lead`; `(market_potential * 0.4 + export_readiness * 0.4 + (1 - overall_risk) * 0.2) * 100` ağırlıklı formülüyle 0–100 arası bir skor hesaplar, eksik veya geçersiz bileşenleri 0.0 olarak varsayar, sonucu `[0, 100]` aralığına sıkıştırır, `score` ve `score_breakdown` (JSONB) değerlerini enjekte edilen repository üzerinden saklar, enjekte edilen session’ı yeniden kullanır ve kendi session’ını açmaz; API anahtarı ve ağ erişimi gerektirmez),
- knowledge base modülü stub’ı (`KnowledgeBaseModule`; `retrieve_context(product_category, target_market)` her zaman `None` döner, `is_enabled` ise `KB_ENABLED` değerini enjekte edilebilir bir settings sağlayıcısı üzerinden lazy olarak okur; import anında yan etki yoktur, henüz embedding, vektör veritabanı veya gerçek bağlam getirme uygulanmamıştır),
- pipeline orkestratörü ve `pipeline_run` takibi (`PipelineOrchestrator.run`; `pipeline_run_id` üretir, `pipeline_runs` kaydını `status="in_progress"` ve `started_at` ile oluşturur, CSV ingestion’ı çağırır, ardından her doğrulanmış lead için `enrich_with_retry` çağırır ve yalnızca başarılı olanları `score_lead` ile skorlar; her lead’i ayrı ayrı izole eder, böylece tek bir hatalı lead tüm akışı durdurmaz; sonunda kaydı `finished_at` ve sayaçlarla `completed`/`failed` olarak günceller; tamamen bağımlılık enjeksiyonludur — `session_factory`, `repository_factory`, ingestion fonksiyonu, enrichment/scorer modülleri, `uuid_factory` ve `clock` enjekte edilebilir — çalışma başına tek bir session açıp tüm lead’ler için yeniden kullanır; import anında ayar yüklemez, session oluşturmaz, API anahtarı ve ağ erişimi gerektirmez),
- veri kalitesi raporu üretimi (`src/pipeline/data_quality.generate_report`; belirli bir `pipeline_run_id` için `raw_leads` (toplam), `validated_leads` (geçerli), `validation_errors` (geçersiz), başarılı ve başarısız `enrichments` ile `scored_leads` sayımlarını yapar ve tek bir `data_quality_reports` satırını enjekte edilen repository üzerinden saklar; orkestratörün tek session’ını yeniden kullanır, kendi session’ını açmaz ve rapor üretimi en iyi çaba ilkesiyle çalışır — raporlama hatası tamamlanmış bir çalışmayı asla `failed` durumuna düşürmez; API anahtarı ve ağ erişimi gerektirmez),
- FastAPI uygulama iskeleti (`src/api/main.py`; `src.api.main:app` olarak import edilebilir, async `lifespan` context manager içerir, deterministik `GET /health` ucu `{"status": "ok"}` döner, `src/database/session.py` içindeki `get_db` veritabanı session bağımlılığını `Depends()` için yeniden dışa aktarır ve Streamlit/front-end dashboard kaynakları için yerel geliştirme CORS yapılandırması sağlar; import anında ayar yüklemez, session oluşturmaz ve veritabanına bağlanmaz),
- FastAPI leads route’ları (`src/api/routes/leads.py`; `GET /leads` skorlanmış lead’leri opsiyonel `min_score` filtresiyle listeler, `GET /leads/filter` açık filtre ucudur ve `GET /leads/{lead_id}` UUID ile tek bir skorlanmış lead döner, kayıt yoksa 404 verir — hepsi `PipelineRepository.get_scored_leads()` / `get_scored_lead_by_id()` üzerinden çalışır ve `src/api/schemas.py` içindeki `ScoredLeadResponse` Pydantic modelleri olarak döner; repository, override edilebilir bir `get_repository` bağımlılığı ile sağlanır, `/leads/filter` ucu `/leads/{lead_id}` ucundan önce tanımlanır ki UUID olarak yorumlanmasın ve testlerde repository bağımlılığı override edildiğinde route’lar `DATABASE_URL` veya `OPENAI_API_KEY` gerektirmez),
- FastAPI pipeline run ve kalite raporu route’ları (`src/api/routes/pipeline_runs.py`; `GET /pipeline-runs` tüm çalışmaları `SELECT * FROM pipeline_runs ORDER BY started_at DESC` ile en yeniden eskiye doğru `PipelineRunResponse` modelleri olarak listeler, `GET /pipeline-runs/{run_id}/report` ise ilgili çalışmanın veri kalitesi raporunu `DataQualityReportResponse` olarak döner — `PipelineRepository.get_quality_report()` üzerinden çalışır — rapor yoksa 404, geçersiz UUID için 422 verir; istek session’ı ve repository, override edilebilir `get_session` / `get_repository` bağımlılıklarıyla sağlanır, böylece testlerde bu bağımlılıklar override edildiğinde route’lar `DATABASE_URL` veya `OPENAI_API_KEY` gerektirmez),
- sentetik örnek veri üretici (`data/sample/generate_sample_data.py`; deterministik, yalnızca standart kütüphane kullanan bir betik — rastgele değer, ağ erişimi, veritabanı veya API anahtarı gerektirmez — `data/sample/leads.csv` dosyasını başlık satırıyla birlikte 20 kurgusal ihracat lead kaydı olarak yazar; `.example` e-posta alan adlarını ve çeşitlendirilmiş `product_category` / `target_market` değerlerini kullanır; 18 şema açısından geçerli satır, idempotency'yi test etmek için birebir aynı iş kimliğine sahip bir kopya satır, `contact_email` eksik bir satır ve `product_category` eksik bir satır içerir),
- unit testler.

Toplam **408 unit test** başarıyla geçmektedir.

Sentetik örnek veri üretici betiği ve `data/sample/leads.csv` dosyası uygulanmıştır. Gelecek aşamalarda gerçek knowledge base bağlam getirme (RAG / vektör arama), gerçek OpenAI enrichment entegrasyonu (üretim bağlantısı), Streamlit dashboard, Docker Compose ve tam PostgreSQL/Docker entegrasyon ile smoke testleri (Task 23) eklenecektir. Pipeline orkestratörü ve veri kalitesi raporu üretimi uygulanmıştır ve sahte bağımlılıklarla unit test edilebilir; tam Docker/PostgreSQL entegrasyonu ise henüz planlanmaktadır.

Bu proje özellikle Data Analyst, Analytics Engineer ve Data Engineer rollerine geçiş sürecinde; veri kalitesi, pipeline tasarımı, database modeling, AI enrichment ve test odaklı geliştirme becerilerini göstermek için hazırlanmıştır.

---

## Status Note

This repository is under active development.  
The current version covers the data layer, per-lead processing and end-to-end orchestration: schema design, validation, database modeling, repository behavior, CSV ingestion, structured logging, mock-mode LLM enrichment with a validation gate, retry orchestration, lead scoring, the pipeline orchestrator with `pipeline_run` lifecycle tracking and data quality report generation.  
A knowledge base stub is in place (`retrieve_context` returns `None`); the orchestrator and data quality report generation are unit-testable with fake dependencies, while full Docker/PostgreSQL integration and smoke tests remain planned. Upcoming iterations will add real knowledge base retrieval (RAG / vector search), production OpenAI wiring, the FastAPI and Streamlit layers, Docker/deployment, and integration and smoke tests.
