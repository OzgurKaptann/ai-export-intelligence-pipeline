# AI Export Intelligence Pipeline

**Spec-driven AI/data pipeline for validating, enriching and scoring export leads.**

This project is being developed as a production-oriented data pipeline, not just a simple AI demo.  
It focuses on clean architecture, database-first design, validation, testing and step-by-step implementation.

> **Current status:** Foundation layer, CSV ingestion, structured logging, the deterministic mock LLM provider and the enrichment prompt builder completed.  
> Idempotency key generation, CSV ingestion, structured logging, the mock LLM provider and the prompt builder are implemented; real LLM enrichment, scoring, FastAPI, Streamlit dashboard and Docker features are planned for upcoming iterations.

---

## Repository Description

Spec-driven AI export intelligence pipeline built with Python, PostgreSQL, SQLAlchemy, Pydantic and Kiro. Currently includes validation schemas, database migrations, ORM models, repository layer and test coverage; ingestion, LLM enrichment, scoring, API and dashboard features are planned.

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
- Unit tests for configuration, schemas, ORM models, repository behavior, CSV ingestion, logging setup, the mock LLM provider and the prompt builder
- **199 passing unit tests**

Planned next:

- Retry policy
- LLM response validation gate
- Lead scoring module
- Pipeline orchestrator
- FastAPI endpoints
- Streamlit dashboard
- Docker Compose setup
- PostgreSQL integration tests
- End-to-end smoke tests

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
| API | FastAPI planned |
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
- enrichment prompt builder behavior.

Latest local result:

```text
199 passed
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
- Pipeline run tracking
- Data quality reporting

### AI Enrichment

- Deterministic mock LLM mode — **implemented**
- Enrichment prompt builder — **implemented**
- Real LLM integration
- Structured JSON response validation
- Retry handling
- Enrichment failure taxonomy
- Prompt and model traceability

### Lead Scoring

- Rule-based lead scoring
- Score breakdown storage
- Export-readiness scoring
- Market-potential scoring
- Risk-adjusted ranking

### API and Dashboard

- FastAPI health and pipeline endpoints
- Scored lead listing
- Pipeline run reports
- Streamlit dashboard
- Lead ranking views
- Quality metrics visualization

### Production Readiness

- Structured logs — **implemented**
- Docker Compose
- PostgreSQL integration tests
- Smoke tests
- README demo flow
- Sample data

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
- unit testler.

Toplam **199 unit test** başarıyla geçmektedir.

Gelecek aşamalarda gerçek LLM enrichment, retry yönetimi, lead scoring, FastAPI endpoint’leri, Streamlit dashboard, Docker Compose ve entegrasyon testleri eklenecektir.

Bu proje özellikle Data Analyst, Analytics Engineer ve Data Engineer rollerine geçiş sürecinde; veri kalitesi, pipeline tasarımı, database modeling, AI enrichment ve test odaklı geliştirme becerilerini göstermek için hazırlanmıştır.

---

## Status Note

This repository is under active development.  
The current version focuses on the foundation layer: schema design, validation, database modeling and repository behavior.  
Upcoming iterations will add the actual pipeline execution flow, CSV ingestion, AI enrichment, scoring, API and dashboard layers.
