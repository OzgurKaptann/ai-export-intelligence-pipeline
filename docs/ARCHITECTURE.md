# Architecture — AI Export Intelligence Pipeline

Detailed architecture notes for the spec-driven AI/data pipeline that validates,
enriches and scores export leads. This document complements the high-level
overview in the [README](../README.md) with component-level interaction
diagrams, the database model, and the design decisions behind enrichment,
scoring, idempotency and the mock-vs-real LLM boundary.

> **Scope honesty.** This is a portfolio-grade, production-*oriented*
> architecture — clean module boundaries, dependency injection, a real
> relational schema and a layered test pyramid. It is **not** a deployed
> production SaaS: there is no authentication, no cloud infrastructure, no
> Kubernetes, no CI/CD, no vector database / RAG, and no monitoring or alerting.
> Those are listed under [Known limitations](#known-limitations).

---

## Table of contents

1. [System overview](#system-overview)
2. [Data flow](#data-flow)
3. [Component responsibilities](#component-responsibilities)
4. [Database model overview](#database-model-overview)
5. [Enrichment flow](#enrichment-flow)
6. [Scoring flow](#scoring-flow)
7. [API and dashboard flow](#api-and-dashboard-flow)
8. [Testing architecture](#testing-architecture)
9. [Idempotency and retry design](#idempotency-and-retry-design)
10. [Mock vs real LLM design](#mock-vs-real-llm-design)
11. [Known limitations](#known-limitations)

---

## System overview

The system is a single Python application organised into clear layers. The
pipeline (ingestion → enrichment → scoring) writes to PostgreSQL; a FastAPI
service reads from PostgreSQL; and a read-only Streamlit dashboard reads from the
FastAPI service over HTTP. The dashboard never touches the database directly.

```mermaid
flowchart LR
    subgraph Sources
        CSV[CSV export lead file]
    end

    subgraph Application["Python application (src/)"]
        ORCH[PipelineOrchestrator]
        ING[CSV Ingestion]
        ENR[LLM Enrichment]
        SCO[Lead Scoring]
        DQ[Data Quality Report]
        API[FastAPI service]
    end

    subgraph Storage
        PG[(PostgreSQL 15)]
    end

    subgraph Presentation
        DASH[Streamlit dashboard]
    end

    CSV --> ORCH
    ORCH --> ING --> PG
    ORCH --> ENR --> PG
    ORCH --> SCO --> PG
    ORCH --> DQ --> PG
    PG --> API
    API -->|HTTP / JSON| DASH
```

Key properties:

- **Dependency injection everywhere.** The orchestrator, enrichment module,
  scorer and data-quality reporter all accept their collaborators (session
  factory, repository, providers, clock, uuid factory) as constructor / call
  arguments. Production defaults are built lazily, never at import time, so
  importing any module has no side effects and reads no configuration.
- **One session per run.** The orchestrator opens exactly one database session
  and reuses it for ingestion, every enrichment and every scoring call.
- **Mock-first.** With `MOCK_LLM_ENABLED=true` (the default) the entire pipeline
  runs deterministically with no API key, no network and no external service.

---

## Data flow

```mermaid
flowchart TD
    A[CSV Export Lead File] --> B[CSV Ingestion Module]
    B --> C[Raw Lead Validation - RawLeadSchema]
    C --> D{Valid Record?}

    D -- No --> E[validation_errors]
    D -- Yes --> F[Idempotency Resolution - skip mode]

    F --> G[raw_leads]
    G --> H[validated_leads]

    H --> I[Optional Knowledge Base Context - stub returns None]
    I --> J[LLM / Mock LLM Enrichment]
    J --> K[Enrichment Schema Validation - EnrichmentOutputSchema]

    K -- Failed --> L[Enrichment failure metadata + retry taxonomy]
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

Stage-by-stage:

1. **Ingestion.** `ingest_csv_file` reads each row with `csv.DictReader`,
   generates a deterministic idempotency key, and validates the row against
   `RawLeadSchema`. Valid rows are written to `raw_leads` **and**
   `validated_leads`; invalid rows are recorded in `validation_errors` and go no
   further. A row whose idempotency key was already ingested is counted as
   `skipped` (skip-mode duplicate handling).
2. **Enrichment.** For each validated lead, the orchestrator calls
   `enrich_with_retry`. The selected provider (mock or real) produces a JSON
   payload that must pass the `EnrichmentOutputSchema` validation gate before it
   is stored as a successful `enrichments` row. Failures are classified into a
   status taxonomy and stored with error metadata.
3. **Scoring.** Only successfully enriched leads are scored. The weighted score
   and a `score_breakdown` are written to `scored_leads`.
4. **Reporting.** After all leads are processed, `generate_report` counts each
   stage and writes one `data_quality_reports` row.
5. **Serving.** FastAPI exposes the scored leads and reports; the dashboard
   renders them.

---

## Component responsibilities

| Component | Module | Responsibility |
|---|---|---|
| Configuration | `src/config.py` | `pydantic-settings` env config; `DATABASE_URL` required, sensible defaults for everything else |
| Validation schemas | `src/validation/` | `RawLeadSchema` (input) and `EnrichmentOutputSchema` (LLM output gate) |
| Idempotency | `src/ingestion/idempotency.py` | Deterministic SHA-256 business-identity key |
| CSV ingestion | `src/ingestion/csv_ingestion.py` | Parse, validate, persist valid/invalid rows; skip duplicates |
| Mock LLM | `src/enrichment/mock_llm.py` | Deterministic, schema-valid synthetic enrichment (seeded by idempotency key) |
| Real LLM | `src/enrichment/real_llm.py` | Optional OpenAI provider (JSON output mode), built lazily |
| Prompt builder | `src/enrichment/prompt_builder.py` | Deterministic prompt text including lead fields + output contract |
| Retry policy | `src/enrichment/retry_policy.py` | Pure classification of the failure taxonomy |
| Enrichment module | `src/enrichment/llm_enrichment.py` | Provider selection, validation gate, failure mapping, retry loop |
| Knowledge base | `src/knowledge_base/kb_module.py` | Stub — `retrieve_context` returns `None`; `is_enabled` reads `KB_ENABLED` |
| Scoring | `src/scoring/lead_scorer.py` | Weighted 0–100 score with breakdown |
| Orchestrator | `src/pipeline/orchestrator.py` | Run lifecycle, single session, per-lead isolation |
| Data quality | `src/pipeline/data_quality.py` | Per-stage row counts → one report row |
| Repository | `src/database/repository.py` | All CRUD; session injected, no global state |
| ORM / session | `src/database/models.py`, `session.py` | SQLAlchemy 2.0 models and lazy session factory |
| API | `src/api/` | FastAPI app + read-only routes |
| Dashboard | `dashboard/app.py` | Streamlit read-only views over the API |

---

## Database model overview

Seven core tables, created by `migrations/001_initial_schema.sql` (every
statement is `IF NOT EXISTS`, so the migration is idempotent).

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

| Table | Purpose |
|---|---|
| `pipeline_runs` | Tracks each pipeline execution and its terminal status + counts |
| `raw_leads` | Deduplicated raw lead records (unique `idempotency_key`) |
| `validated_leads` | Schema-valid lead records |
| `enrichments` | LLM/mock enrichment outputs and failure metadata |
| `scored_leads` | Final scored leads (denormalised `company_name` / `product_category`) |
| `data_quality_reports` | Run-level quality metrics |
| `validation_errors` | Per-field validation failures |

---

## Enrichment flow

```mermaid
sequenceDiagram
    participant ORCH as Orchestrator
    participant ENR as LLMEnrichmentModule
    participant PROV as Provider (Mock or Real)
    participant GATE as EnrichmentOutputSchema gate
    participant REPO as Repository

    ORCH->>ENR: enrich_with_retry(lead, idempotency_key, run_id, session)
    ENR->>PROV: generate(lead, prompt)
    alt MOCK_LLM_ENABLED=true (default)
        PROV-->>ENR: deterministic JSON (seeded by idempotency_key)
    else MOCK_LLM_ENABLED=false
        PROV-->>ENR: OpenAI JSON response (response_format=json_object)
    end
    ENR->>GATE: model_validate(payload)
    alt valid
        GATE-->>ENR: EnrichmentOutputSchema
        ENR->>REPO: insert enrichment (status=success)
    else invalid / error
        GATE-->>ENR: ValidationError
        ENR->>REPO: insert enrichment (status=validation_failed / timeout / ...)
        Note over ENR: retry only transient statuses<br/>timeout, network_error, rate_limited
    end
```

The **validation gate** is the architectural heart of enrichment: both the mock
and real providers converge on the same `EnrichmentOutputSchema.model_validate`
call. Nothing reaches the `enrichments` table as `success` unless it satisfies
the schema (floats in `[0, 1]`, a `risk_assessment` with `overall_risk`, a
`recommended_markets` list). Failures are mapped onto a status taxonomy:
`success`, `validation_failed`, `empty_response`, `invalid_json`,
`timeout`, `network_error`, `rate_limited`, `unknown_error`.

---

## Scoring flow

```mermaid
flowchart LR
    A[EnrichmentOutputSchema] --> B[LeadScorerModule.score_lead]
    B --> C["score = (market_potential*0.4<br/>+ export_readiness*0.4<br/>+ (1 - overall_risk)*0.2) * 100"]
    C --> D[clamp to 0..100]
    D --> E[scored_leads: score + score_breakdown JSONB]
```

- Missing or invalid components default to `0.0` for that component.
- The result is clamped to `[0, 100]` (a property test asserts this universally).
- `score_breakdown` records each weighted component so the score is explainable.

---

## API and dashboard flow

```mermaid
sequenceDiagram
    participant USER as Browser
    participant DASH as Streamlit dashboard
    participant API as FastAPI
    participant REPO as Repository
    participant PG as PostgreSQL

    USER->>DASH: open http://localhost:8501
    DASH->>API: GET /leads?min_score=...
    API->>REPO: get_scored_leads(min_score)
    REPO->>PG: SELECT FROM scored_leads
    PG-->>REPO: rows
    REPO-->>API: ScoredLeadResponse[]
    API-->>DASH: JSON
    DASH-->>USER: tables + charts
```

Endpoints (all read-only):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe → `{"status": "ok"}` |
| `GET` | `/leads` | List scored leads, optional `min_score` |
| `GET` | `/leads/filter?min_score=` | Explicit filter endpoint |
| `GET` | `/leads/{lead_id}` | Single scored lead (404 if missing) |
| `GET` | `/pipeline-runs` | All runs, newest first |
| `GET` | `/pipeline-runs/{run_id}/report` | Run's data quality report (404 if missing) |

The dashboard is **read-only** and **depends on FastAPI data availability**: it
calls the endpoints with `requests` and shows a friendly message (not a stack
trace) when the API is unreachable. It makes no API call at import time and
never connects to the database directly.

---

## Testing architecture

```mermaid
flowchart TD
    U[Unit tests - tests/unit<br/>fast, offline, no DB] --> CI
    P[Property tests - tests/properties<br/>Hypothesis, 100 examples each] --> CI
    S[Smoke test - tests/smoke<br/>end-to-end, real PostgreSQL] --> CI
    I[Integration tests - tests/integration<br/>real PostgreSQL, all routes] --> CI
    CI[docker-compose.test.yml<br/>runs all four against containerised PostgreSQL]
```

- **Unit** (`tests/unit/`) — pure logic with injected fakes; no PostgreSQL, no
  network, no OpenAI key. **435 passed** locally.
- **Property** (`tests/properties/`) — six universal properties exercised with
  100 generated examples each via Hypothesis; fully offline. **23 passed**.
- **Smoke** (`tests/smoke/`) — runs the whole pipeline against a real local
  PostgreSQL; **skipped** unless `SMOKE_TEST_DATABASE_URL` is set.
- **Integration** (`tests/integration/`) — real components against a real
  PostgreSQL; **skipped** unless `DATABASE_URL` is set. The live OpenAI test is
  skipped unless `OPENAI_API_KEY` **and** `RUN_LIVE_LLM_TESTS=true` are both set.

The containerised full suite (`docker-compose.test.yml`) runs unit + property +
smoke + integration together against a dedicated PostgreSQL: **474 passed, 3
skipped** (the 3 skips are the live-LLM tests that need a real key).

---

## Idempotency and retry design

**Idempotency key.** `generate_idempotency_key` builds a deterministic SHA-256
hash from the lead's *business identity* — `company_name`, `contact_email`,
`product_category` and `target_market` — after normalising whitespace, casing
and empty/missing values. The same logical lead always produces the same key.

**Skip mode (implemented).** When a validated row's idempotency key already
exists in `raw_leads` (which has a unique constraint on `idempotency_key`), the
row is counted as `skipped` instead of being inserted again. This keeps a re-run
from crashing on the unique constraint.

> **Not implemented yet:** `update` and `reprocess` idempotency modes. The
> `IDEMPOTENCY_MODE` setting exists and defaults to `skip`; only `skip` is wired
> up. Documenting these honestly matters — they are future work, not current
> behaviour.

**Retry taxonomy.** Enrichment failures are classified by `retry_policy`:

```mermaid
flowchart LR
    F[Enrichment failure] --> C{Status retryable?}
    C -- timeout / network_error / rate_limited --> R[Retry with backoff]
    C -- validation_failed / invalid_json / empty_response / unknown_error --> N[No retry]
    R --> W["wait = RETRY_DELAY_SECONDS * 2^retry_count + jitter"]
    W --> M{retry_count < RETRY_MAX_ATTEMPTS?}
    M -- yes --> R
    M -- no --> N
```

Only transient statuses are retried; `retry_count` is incremented in the
database and never exceeds `RETRY_MAX_ATTEMPTS` (a property test asserts the
ceiling). Sleep/backoff are injectable so tests run with no real delay.

---

## Mock vs real LLM design

```mermaid
flowchart TD
    E[LLMEnrichmentModule.enrich_lead] --> Q{MOCK_LLM_ENABLED?}
    Q -- true - default --> MOCK[MockLLMProvider<br/>deterministic, seeded, offline]
    Q -- false --> REAL[RealLLMProvider<br/>OpenAI SDK, needs OPENAI_API_KEY]
    MOCK --> G[EnrichmentOutputSchema gate]
    REAL --> G
    G --> DB[(enrichments)]
```

- **Mock is the default** (`MOCK_LLM_ENABLED=true`). The mock provider returns a
  deterministic, schema-valid payload seeded by the lead's idempotency key — the
  full pipeline runs, is tested and is demoed with **no OpenAI key**, no network
  and no cost.
- **Real is optional** (`MOCK_LLM_ENABLED=false`). `RealLLMProvider` uses the
  OpenAI SDK with `response_format={"type": "json_object"}`, reads
  `OPENAI_API_KEY` / `OPENAI_MODEL` from config, builds the client lazily (never
  at import time), and records the actual model from the API response. Real mode
  fails clearly if `OPENAI_API_KEY` is missing.
- **`OPENAI_API_KEY` is not required** for the default demo, the unit/property
  tests, Docker, or the smoke/integration suites.
- The single validation gate means both providers are interchangeable — the rest
  of the pipeline cannot tell which one ran.

---

## Known limitations

These are deliberately **not** implemented and are documented here so the
architecture is not oversold:

- **No real knowledge base / RAG.** `KnowledgeBaseModule.retrieve_context`
  returns `None`; there is no vector database, embeddings or retrieval.
- **Idempotency:** only `skip` mode. `update` and `reprocess` are not wired up.
- **No authentication / authorisation** on the API or dashboard.
- **No cloud infrastructure, Kubernetes, CI/CD, monitoring or alerting.**
- **No real buyer/seller data or matching** — all sample data is synthetic and
  fictional (`.example` domains).
- **Dashboard is read-only** and depends on the FastAPI service being up.
- **Real OpenAI mode is opt-in**, not the default; the live LLM test is skipped
  by default.

See the README's *Future Enhancements* section for the roadmap.
