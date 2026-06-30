# AI Export Intelligence Pipeline

**A spec-driven AI/data pipeline that ingests, validates, enriches, scores and
serves export leads — with a relational audit trail, a read-only API and a
dashboard.**

[![tests](https://img.shields.io/badge/unit%20tests-435%20passed-brightgreen)](#running-tests)
[![property tests](https://img.shields.io/badge/property%20tests-23%20passed-brightgreen)](#running-tests)
[![python](https://img.shields.io/badge/python-3.11-blue)](#tech-stack)
[![LLM](https://img.shields.io/badge/LLM-mock%20default%20%7C%20OpenAI%20optional-orange)](#mock-vs-real-llm)

> **Portfolio-grade, production-*oriented* architecture — not a deployed
> production SaaS.** It demonstrates clean module boundaries, dependency
> injection, a real PostgreSQL schema and a layered test pyramid. It has **no**
> authentication, cloud infrastructure, Kubernetes, CI/CD, vector DB/RAG,
> monitoring or alerting, and uses only **synthetic, fictional** lead data. See
> [Future Enhancements](#future-enhancements).

For deeper detail see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and the
step-by-step [`docs/DEMO.md`](docs/DEMO.md).

---

## Table of contents

- [Project Purpose](#project-purpose)
- [Tech Stack](#tech-stack)
- [Architecture Overview](#architecture-overview)
- [Getting Started in 3 Minutes](#getting-started-in-3-minutes)
- [Setup Instructions](#setup-instructions)
- [Running the Pipeline](#running-the-pipeline)
- [Running Tests](#running-tests)
- [API Documentation](#api-documentation)
- [Sample Output](#sample-output)
- [Technology Choices](#technology-choices)
- [Future Enhancements](#future-enhancements)
- [Türkçe Özet (Turkish Summary)](#türkçe-özet-turkish-summary)

---

## Project Purpose

Export teams often work with scattered lead data from CSV files, spreadsheets,
CRM exports or manual lists. This project is an **AI-powered export lead
intelligence pipeline** that turns raw CSV lead data into scored, queryable
insight. It can:

- **ingest** raw export lead data from CSV,
- **validate** each lead record against a strict schema,
- **prevent duplicate processing** with deterministic idempotency keys,
- **enrich** leads with a mock LLM (default) or a real OpenAI provider (optional),
- **score** leads 0–100 based on export potential, with an explainable breakdown,
- **persist** every pipeline stage in PostgreSQL for a full audit trail,
- **expose** results through a FastAPI service,
- and **visualize** insights in a Streamlit dashboard.

It is built for **data engineering / analytics engineering portfolio value**:
the goal is to show clean boundaries between validation, storage, enrichment,
scoring and reporting, backed by unit, property, smoke and integration tests.

---

## Tech Stack

| Area | Technology |
|---|---|
| Language | Python 3.11 |
| API | FastAPI |
| Database | PostgreSQL 15 |
| ORM | SQLAlchemy 2.0 |
| Validation | Pydantic v2 (+ `pydantic-settings`) |
| AI enrichment | Mock LLM (default) · OpenAI SDK (optional) |
| Dashboard | Streamlit (4-page, read-only) |
| Containerization | Docker + Docker Compose |
| Testing | pytest |
| Property testing | Hypothesis |
| Logging | structlog (JSON/console) |
| Migrations | Raw SQL scripts |

---

## Architecture Overview

The pipeline reads a CSV, validates and deduplicates each lead, enriches it
through a mock or real LLM behind a strict validation gate, scores it, and writes
every stage to PostgreSQL. FastAPI serves the results; the read-only Streamlit
dashboard renders them over HTTP.

```mermaid
flowchart TD
    A[CSV Export Lead File] --> B[CSV Ingestion Module]
    B --> C[Raw Lead Validation]
    C --> D{Valid Record?}

    D -- No --> E[validation_errors]
    D -- Yes --> F[Idempotency Resolution - skip mode]

    F --> G[raw_leads]
    G --> H[validated_leads]

    H --> I[Optional Knowledge Base Context - stub]
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

The seven core PostgreSQL tables (created by
`migrations/001_initial_schema.sql`):

| Table | Purpose |
|---|---|
| `pipeline_runs` | Tracks each pipeline execution and its status + counts |
| `raw_leads` | Deduplicated raw lead records (unique `idempotency_key`) |
| `validated_leads` | Schema-valid lead records |
| `enrichments` | LLM/mock enrichment outputs and failure metadata |
| `scored_leads` | Final scored leads (denormalised company / category) |
| `data_quality_reports` | Run-level quality metrics |
| `validation_errors` | Per-field validation failures |

```mermaid
erDiagram
    pipeline_runs ||--o{ raw_leads : contains
    pipeline_runs ||--o{ validated_leads : tracks
    pipeline_runs ||--o{ enrichments : tracks
    pipeline_runs ||--o{ scored_leads : tracks
    pipeline_runs ||--|| data_quality_reports : summarizes
    pipeline_runs ||--o{ validation_errors : logs
    raw_leads ||--o{ validated_leads : validates
    validated_leads ||--o{ enrichments : enriches
    enrichments ||--o{ scored_leads : produces
```

The full entity-relationship diagram, component interaction sequences and the
enrichment/scoring/retry diagrams live in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Getting Started in 3 Minutes

The whole stack — PostgreSQL, the FastAPI app and the Streamlit dashboard — runs
with Docker Compose. **No `.env` file, no OpenAI key and no network are
required**: the app runs in mock-LLM mode (`MOCK_LLM_ENABLED=true`) with all
configuration wired through `environment:` in the compose file (no secrets are
baked into the images).

```powershell
# 1. Build and start db + app + dashboard
docker compose up --build -d

# 2. Verify the API is healthy
Invoke-RestMethod http://localhost:8000/health   # -> status: ok

# 3. Open the dashboard in your browser
#    http://localhost:8501
```

On startup the `app` container runs `migrations/run_migrations.py` and then
`uvicorn`, so the schema is created automatically. To process the bundled sample
data, see [Running the Pipeline](#running-the-pipeline).

Tear everything down (and remove the PostgreSQL volume):

```powershell
docker compose down -v
```

---

## Setup Instructions

### Option A — Docker (recommended)

See [Getting Started in 3 Minutes](#getting-started-in-3-minutes). This is the
simplest path and needs only Docker.

### Option B — Local Python

```powershell
# Create/activate a virtual environment, then:
pip install -r requirements.txt
```

You will need a local PostgreSQL 15 for anything that touches the database
(running the pipeline, the smoke test, the integration tests). The unit and
property suites need no database.

```powershell
# Apply the schema to your database
$env:DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/ai_export"
python migrations/run_migrations.py

# Start the API
uvicorn src.api.main:app --reload

# Start the dashboard (separate terminal)
$env:API_BASE_URL="http://localhost:8000"
streamlit run dashboard/app.py
```

### Environment variables

Use [`.env.example`](.env.example) as the reference. Key variables:

```text
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/ai_export   # required
MOCK_LLM_ENABLED=true        # default: deterministic mock LLM, no key needed
OPENAI_API_KEY=              # only needed when MOCK_LLM_ENABLED=false
OPENAI_MODEL=gpt-4o-mini     # only used in real mode
KB_ENABLED=false             # knowledge base is a stub
IDEMPOTENCY_MODE=skip        # only 'skip' is implemented
RETRY_MAX_ATTEMPTS=3
LOG_LEVEL=INFO
```

`DATABASE_URL` is **required** (the app exits with a clear message if it is
missing). Everything else has a sensible default.

### Mock vs real LLM

- **Default: mock mode** (`MOCK_LLM_ENABLED=true`). Deterministic, schema-valid
  enrichment seeded by each lead's idempotency key. **No `OPENAI_API_KEY` is
  required** for the demo, Docker, or any default test flow.
- **Optional: real mode** (`MOCK_LLM_ENABLED=false` + a valid `OPENAI_API_KEY`).
  Uses the OpenAI SDK with JSON output mode. Real mode fails clearly if the key
  is missing. **Real OpenAI mode is not the default.**

---

## Running the Pipeline

The pipeline is driven by `PipelineOrchestrator.run(file_path)` in
[`src/pipeline/orchestrator.py`](src/pipeline/orchestrator.py). It generates a
`pipeline_run_id`, ingests the CSV, enriches and scores each validated lead
(isolating per-lead failures so one bad row never stops the run), writes a data
quality report, and updates the run to `completed`.

> **There is no standalone CLI module** (no `src.pipeline.run_pipeline`). The
> orchestrator is the entry point: it is exercised end-to-end by the smoke and
> integration tests, and can be invoked directly as shown below.

### Docker path

The bundled sample CSV (`data/sample/leads.csv`) is baked into the app image,
and `DATABASE_URL` / `MOCK_LLM_ENABLED` are already set in `docker-compose.yml`,
so you can run the orchestrator inside the running `app` container:

```powershell
docker compose exec app python -c "from src.pipeline.orchestrator import PipelineOrchestrator; print(PipelineOrchestrator().run('data/sample/leads.csv'))"
```

### Local path

```powershell
$env:DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/ai_export"
$env:MOCK_LLM_ENABLED="true"
python -c "from src.pipeline.orchestrator import PipelineOrchestrator; print(PipelineOrchestrator().run('data/sample/leads.csv'))"
```

### Sample data and expected outcome

`data/sample/leads.csv` has 20 synthetic rows: 18 schema-valid (one of which is
an exact business-identity **duplicate**) and 2 invalid (one missing
`contact_email`, one missing `product_category`). After one run you should see a
`completed` `PipelineRunResult` with **17 ingested, 17 enriched, 17 scored, 2
invalid, 1 skipped duplicate**. The same flow is asserted automatically by the
smoke test — see [Running Tests](#running-tests).

---

## Running Tests

Install dependencies first: `pip install -r requirements.txt`.

The suite is a four-layer pyramid. The unit and property layers are fast,
offline and need no database, key or network; the smoke and integration layers
require a real PostgreSQL and are **skipped** when their database URL is unset.

### Unit tests (fast, offline)

```bash
python -m pytest tests/unit/ -v
```

→ **435 passed** locally.

### Property-based tests (Hypothesis, 100 examples each)

```bash
python -m pytest tests/properties/ -v --hypothesis-show-statistics
```

→ **23 passed**. Six universal properties: CSV record-count preservation,
required-field enforcement, validation-error field attribution, the enrichment
status taxonomy, the retry-count ceiling and idempotency-key determinism. Fully
offline.

### Smoke test (requires local PostgreSQL)

Runs the whole pipeline end-to-end against `data/sample/leads.csv`. **Skipped**
unless `SMOKE_TEST_DATABASE_URL` is set, and it only truncates a database whose
URL contains `test`, `smoke` or `ci`.

```powershell
$env:SMOKE_TEST_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/ai_export_smoke"
python -m pytest tests/smoke/ -v
```

### Integration tests (requires local PostgreSQL)

Real components against a real PostgreSQL. Reads `DATABASE_URL` and is
**skipped** when it is unset.

```powershell
$env:DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/ai_export_test"
python -m pytest tests/integration/ -v
```

### Containerized full suite (recommended for a complete run)

Spins up a dedicated PostgreSQL and runs unit + property + smoke + integration
together:

```powershell
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from test
docker compose -f docker-compose.test.yml down -v
```

→ **474 passed, 3 skipped**. The 3 skips are the live-LLM tests
(`tests/integration/test_real_llm.py`, marked `live_llm`), which require a real
key and run only when both `OPENAI_API_KEY` and `RUN_LIVE_LLM_TESTS=true` are
set.

> **A note on warnings (honest, not alarming):** you may see a Starlette
> `PendingDeprecationWarning` about `python_multipart`, and — depending on your
> pytest config — an "unknown marker" warning for `live_llm`. Neither affects
> correctness; all tests pass.

---

## API Documentation

The FastAPI service exposes read-only endpoints. Interactive OpenAPI docs are at
**http://localhost:8000/docs** when the stack is running.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe → `{"status": "ok"}` |
| `GET` | `/leads` | List scored leads; optional `?min_score=` filter |
| `GET` | `/leads/filter?min_score=` | Explicit filter endpoint |
| `GET` | `/leads/{lead_id}` | Single scored lead by UUID (404 if not found) |
| `GET` | `/pipeline-runs` | All pipeline runs, newest first |
| `GET` | `/pipeline-runs/{run_id}/report` | Run's data quality report (404 if none) |

Examples (PowerShell):

```powershell
Invoke-RestMethod http://localhost:8000/leads
Invoke-RestMethod "http://localhost:8000/leads/filter?min_score=60"
Invoke-RestMethod http://localhost:8000/pipeline-runs
```

The dashboard is a **read-only** view over these endpoints — it never touches
the database directly and shows a friendly message (not a stack trace) when the
API is unreachable. Start the API first, then point the dashboard at it via
`API_BASE_URL` (default `http://localhost:8000`).

---

## Sample Output

### A scored lead (`GET /leads/{lead_id}`)

A scored lead record exposes these fields (see
[`src/api/schemas.py`](src/api/schemas.py)):

| Field | Meaning |
|---|---|
| `scored_lead_id` | UUID of this scored lead |
| `validated_lead_id` | Link back to the validated lead |
| `enrichment_id` | Link to the enrichment that produced the score |
| `pipeline_run_id` | The run that produced it |
| `company_name` | Denormalised company name |
| `product_category` | Denormalised product category |
| `score` | Final 0–100 lead score |
| `score_breakdown` | JSON of the weighted components (explainable score) |
| `scored_at` | Timestamp |

```json
{
  "scored_lead_id": "…",
  "validated_lead_id": "…",
  "enrichment_id": "…",
  "pipeline_run_id": "…",
  "company_name": "Aurora Industrial Coatings",
  "product_category": "Industrial Coatings",
  "score": 71.4,
  "score_breakdown": {
    "market_potential": 0.78,
    "export_readiness": 0.69,
    "risk_adjustment": 0.62
  },
  "scored_at": "2026-06-30T12:00:00Z"
}
```

> Exact values are deterministic per lead under the mock LLM but illustrative
> here. The enrichment's model identifier is stored on the `enrichments` row
> (e.g. `mock-llm-v1` in mock mode, or the actual OpenAI model in real mode).

### A data quality report (`GET /pipeline-runs/{run_id}/report`)

| Field | Meaning |
|---|---|
| `total_records` | Rows in `raw_leads` (ingested) |
| `valid_records` | Rows in `validated_leads` |
| `invalid_records` | Rows in `validation_errors` |
| `enriched_records` | Enrichments with `status = success` |
| `failed_enrichments` | Enrichments with any non-success status |
| `scored_records` | Rows in `scored_leads` |

For the bundled sample data the report shows `total=17, valid=17, invalid=2,
enriched=17, failed=0, scored=17` (the 18th valid row is the skipped duplicate).

---

## Technology Choices

- **Pydantic v2** — schema-first validation at two critical boundaries: untrusted
  CSV input (`RawLeadSchema`) and untrusted LLM output (`EnrichmentOutputSchema`).
  The LLM **validation gate** means nothing reaches the `enrichments` table as
  `success` unless it satisfies the contract — this is what makes an LLM step
  safe in a data pipeline.
- **PostgreSQL** — a real relational store gives a queryable, auditable trail
  across seven tables, with a unique constraint enforcing idempotency and JSONB
  for flexible payloads (`score_breakdown`, `risk_assessment`, `raw_csv_row`).
- **Mock LLM first** — implementing a deterministic mock provider before any real
  integration means the entire pipeline is runnable, testable and demoable with
  **no API key, no network and zero cost**, and tests are reproducible.
- **Repository + session injection** — modules receive their database session and
  collaborators rather than creating global state. Defaults are built lazily, so
  importing a module has no side effects, and tests inject fakes (no real DB or
  key needed for unit tests).
- **Docker Compose** — one command brings up db + app + dashboard with all config
  wired through `environment:` and no baked-in secrets; a separate test compose
  runs the full suite against a real PostgreSQL.
- **Hypothesis / property tests** — some invariants (score always in `[0, 100]`,
  retry count never exceeds the max, idempotency-key determinism, CSV record-count
  preservation) are best proven across 100 generated examples, not a handful of
  hand-picked cases.

---

## Future Enhancements

These are **planned, not implemented** — listed honestly so the scope is clear:

- **Real knowledge base / RAG** — replace the `KnowledgeBaseModule` stub with
  real context retrieval (embeddings + vector search).
- **`update` and `reprocess` idempotency modes** — only `skip` is implemented today.
- **Authentication / authorisation** on the API and dashboard.
- **Monitoring & observability** — metrics, tracing, alerting.
- **CI/CD** — automated pipelines for test + build + deploy.
- **Cloud deployment** — managed PostgreSQL and container hosting.
- **Richer dashboard filters** — segment by category, market, run, date.
- **Buyer/seller matching** — connect scored leads to buyer profiles.

None of the above are present in the current codebase.

---

## Türkçe Özet (Turkish Summary)

### Projenin amacı

Bu proje, ihracat potansiyeli olan firma ve lead verilerini uçtan uca işlemek
için tasarlanan **AI destekli bir veri pipeline** çalışmasıdır. CSV gibi ham veri
kaynaklarından gelen lead kayıtlarını doğrular, tekrar eden kayıtları idempotency
mantığıyla yönetir, **mock LLM (varsayılan)** veya **opsiyonel gerçek OpenAI** ile
zenginleştirir, ihracat potansiyeline göre 0–100 arası skorlar ve tüm aşamaları
PostgreSQL üzerinde izlenebilir biçimde saklar. Sonuçlar bir FastAPI servisiyle
sunulur ve salt-okunur bir Streamlit dashboard ile görselleştirilir.

> Bu, **portföy düzeyinde, üretime yönelik** bir mimaridir; **canlıya alınmış bir
> üretim SaaS değildir**. Kimlik doğrulama, bulut altyapısı, Kubernetes, CI/CD,
> vektör DB/RAG, monitoring veya alerting **yoktur** ve tüm veriler sentetiktir.

### Nasıl çalıştırılır

Tüm yığın (PostgreSQL + FastAPI + Streamlit) tek komutla ayağa kalkar. OpenAI
anahtarı, `.env` dosyası veya ağ erişimi **gerekmez** (mock LLM modu varsayılan):

```powershell
docker compose up --build -d
Invoke-RestMethod http://localhost:8000/health   # -> status: ok
# Dashboard: http://localhost:8501
docker compose down -v
```

Örnek veriyle pipeline'ı çalıştırmak için (ayrı bir CLI **yoktur**; orkestratör
doğrudan çağrılır):

```powershell
docker compose exec app python -c "from src.pipeline.orchestrator import PipelineOrchestrator; print(PipelineOrchestrator().run('data/sample/leads.csv'))"
```

### Testler

```bash
python -m pytest tests/unit/ -v          # 435 test (hızlı, çevrimdışı, DB gerektirmez)
python -m pytest tests/properties/ -v    # 23 property test (Hypothesis)
```

Tam paket (`docker-compose.test.yml`) gerçek bir PostgreSQL'e karşı unit +
property + smoke + integration testlerini birlikte çalıştırır: **474 passed, 3
skipped** (3 skip, gerçek anahtar gerektiren canlı LLM testleridir). Smoke ve
integration testleri ilgili `DATABASE_URL` ayarlı değilse atlanır.

### Mimari değer

Bu proje özellikle **Data Analyst, Analytics Engineer ve Data Engineer**
rollerine geçiş sürecinde; veri kalitesi, pipeline tasarımı, database modeling,
AI enrichment (katı bir doğrulama kapısıyla) ve test odaklı geliştirme
becerilerini göstermek için hazırlanmıştır. Öne çıkan tasarım kararları: tek
oturumlu (single-session) orkestrasyon, bağımlılık enjeksiyonu, deterministik
idempotency anahtarı ve `EnrichmentOutputSchema` doğrulama kapısı.

### Mock LLM (varsayılan) / gerçek OpenAI (opsiyonel)

Varsayılan olarak **mock LLM** kullanılır; bu sayede pipeline anahtarsız, ağsız ve
maliyetsiz biçimde çalışır ve testler deterministiktir. **`OPENAI_API_KEY`
varsayılan demo ve test akışları için gerekmez.** Gerçek modu kullanmak için
`MOCK_LLM_ENABLED=false` yapıp geçerli bir anahtar sağlamak yeterlidir; gerçek
API çağrısı yapan canlı test (`live_llm`) varsayılan olarak atlanır ve yalnızca
hem `OPENAI_API_KEY` hem de `RUN_LIVE_LLM_TESTS=true` ayarlandığında çalışır.

---

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — detailed component interaction
  diagrams, database model, enrichment/scoring/retry design, testing
  architecture and known limitations.
- [`docs/DEMO.md`](docs/DEMO.md) — a copy-paste, step-by-step demo script for
  reviewers and interviewers.
- [`.kiro/specs/ai-export-intelligence-pipeline/`](.kiro/specs/ai-export-intelligence-pipeline/)
  — the requirements and task plan that drove this spec-driven build.
