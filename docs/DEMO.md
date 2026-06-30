# Demo Script — AI Export Intelligence Pipeline

A step-by-step walkthrough for a **reviewer, interviewer or hiring manager**. It
takes ~5 minutes and needs only Docker. No OpenAI key, no cloud account and no
network access to an LLM are required — the pipeline runs in deterministic
**mock-LLM mode** by default.

Commands are written for **PowerShell on Windows** (the project's primary
shell). They work on macOS/Linux too; only `Invoke-RestMethod` would change to
`curl`.

> Throughout this demo we use modern `docker compose` (a space), not the legacy
> `docker-compose` binary.

---

## What this proves (talk track)

> "This project demonstrates a production-*oriented* data pipeline: schema-first
> validation, idempotent ingestion, an LLM enrichment step gated by a strict
> output schema, explainable scoring, a relational audit trail across seven
> tables, a read-only API, a dashboard, and a four-layer test pyramid — all
> runnable end-to-end with a single `docker compose up` and **no API key**."

Keep these three lines for the end of the demo:

- **What this proves** — clean architecture, dependency injection, data-quality
  thinking and test discipline, not just an LLM call.
- **Why this architecture matters** — the validation gate, single-session
  orchestration and idempotency make the pipeline safe to re-run and easy to
  reason about; mock-first makes it cheap and deterministic to test.
- **What I would build next** — real RAG context, `update`/`reprocess`
  idempotency modes, auth, observability and CI/CD (see *Future Enhancements*).

---

## Prerequisites

- Docker Desktop (with `docker compose`) running.
- This repository checked out locally.
- Ports `8000` (API), `8501` (dashboard) and `5432` (PostgreSQL) free.

No `.env` file is needed — the compose file provides safe local defaults
(`MOCK_LLM_ENABLED=true`, empty `OPENAI_API_KEY`).

---

## 1. Start the Docker stack

```powershell
docker compose up --build -d
docker compose ps
```

You should see three services — `db`, `app`, `dashboard`. The `app` container
runs the database migration (`migrations/run_migrations.py`) and then `uvicorn`,
so the schema is created automatically. Wait until `db` and `app` report
`healthy`.

---

## 2. Verify API health

```powershell
Invoke-RestMethod http://localhost:8000/health
# -> status : ok
```

Open the interactive OpenAPI docs in a browser to show the full API surface:

```
http://localhost:8000/docs
```

---

## 3. Run the pipeline against the sample data

> **Note on the entry point.** This project does **not** ship a standalone CLI
> module (there is no `src.pipeline.run_pipeline`). The pipeline is driven by
> `PipelineOrchestrator.run(...)`, which is exercised by the smoke and
> integration tests and can be invoked directly. The command below calls that
> existing orchestrator inside the running `app` container — it adds no new code
> and uses the `DATABASE_URL` / `MOCK_LLM_ENABLED` already configured in
> `docker-compose.yml`. The sample CSV (`data/sample/leads.csv`) is baked into
> the image.

```powershell
docker compose exec app python -c "from src.pipeline.orchestrator import PipelineOrchestrator; print(PipelineOrchestrator().run('data/sample/leads.csv'))"
```

Expected result (printed `PipelineRunResult`): a `completed` status with
`total_records=20`, `valid_records=17`, `invalid_records=2`,
`enriched_records=17`, `failed_enrichments=0`, `scored_records=17`.

Why those numbers? The sample CSV has 20 rows: 18 schema-valid (one of which is
an exact business-identity **duplicate**, so it is **skipped**) and 2 invalid
(one missing `contact_email`, one missing `product_category`). So 17 leads are
ingested, enriched (the mock LLM never fails) and scored.

> **Alternative — no manual run needed.** The same end-to-end flow is verified
> automatically by the smoke test. If you prefer to *prove* it rather than run
> it by hand, jump to [step 8](#8-run-the-test-suite) and run the containerised
> suite; the smoke test executes exactly this pipeline and asserts every table.

---

## 4. Show the logs

```powershell
docker compose logs app --tail 40
```

Look for the structured (`structlog`) events: `pipeline_run_started`,
`data_quality_report_generated` and `pipeline_run_completed` with the per-stage
counts. This is the audit trail the pipeline emits for every run.

---

## 5. Query the API

```powershell
# All scored leads
Invoke-RestMethod http://localhost:8000/leads

# Only strong leads (score >= 60)
Invoke-RestMethod "http://localhost:8000/leads/filter?min_score=60"

# All pipeline runs (newest first)
$runs = Invoke-RestMethod http://localhost:8000/pipeline-runs
$runs

# The data quality report for the most recent run
$runId = $runs[0].pipeline_run_id
Invoke-RestMethod "http://localhost:8000/pipeline-runs/$runId/report"
```

Point out in the response:

- A scored lead carries `company_name`, `product_category`, `score`,
  `score_breakdown` (the explainable component weights), plus the linking ids
  (`scored_lead_id`, `validated_lead_id`, `enrichment_id`, `pipeline_run_id`)
  and `scored_at`.
- The quality report carries `total_records`, `valid_records`,
  `invalid_records`, `enriched_records`, `failed_enrichments`, `scored_records`.

---

## 6. View the dashboard

Open:

```
http://localhost:8501
```

Walk through the four sidebar pages:

1. **Overview** — total leads, average score, score-distribution chart.
2. **Lead List** — a 0–100 score slider that filters the table live.
3. **Lead Detail** — pick a lead and inspect its full enrichment JSON.
4. **Pipeline Runs** — the runs and their data quality reports.

Mention that the dashboard is **read-only** and simply calls the FastAPI
endpoints over HTTP — if the API were down it would show a friendly message
rather than crash.

---

## 7. Explain idempotency

Re-run the pipeline on the same file:

```powershell
docker compose exec app python -c "from src.pipeline.orchestrator import PipelineOrchestrator; print(PipelineOrchestrator().run('data/sample/leads.csv'))"
```

Talking points:

- The deterministic **idempotency key** (SHA-256 over the lead's business
  identity: company, email, product category, target market) means the duplicate
  row in the sample is detected and counted as **skipped** rather than inserted
  twice — `raw_leads.idempotency_key` is unique.
- **Skip mode is implemented.** The `update` and `reprocess` modes are **not
  implemented yet** (`IDEMPOTENCY_MODE` defaults to `skip`).

---

## 8. Explain mock vs real LLM

- **Mock is the default** (`MOCK_LLM_ENABLED=true`). The mock provider returns a
  deterministic, schema-valid enrichment seeded by the idempotency key — so the
  whole demo is reproducible and free. **No `OPENAI_API_KEY` is required.**
- **Real is optional** (`MOCK_LLM_ENABLED=false` + a valid `OPENAI_API_KEY`).
  `RealLLMProvider` uses the OpenAI SDK with JSON output mode and records the
  actual model. Both providers pass through the *same* `EnrichmentOutputSchema`
  validation gate, so the rest of the pipeline is provider-agnostic.
- The **live LLM test is skipped by default**; it runs only when both
  `OPENAI_API_KEY` and `RUN_LIVE_LLM_TESTS=true` are set.

---

## 9. Run the test suite

Fast, offline checks on the host (no database needed):

```powershell
python -m pytest tests/unit/ -v
python -m pytest tests/properties/ -v --hypothesis-show-statistics
```

The full containerised suite (unit + property + smoke + integration) against a
real PostgreSQL:

> **Tear the demo stack down first.** Both compose files share this directory's
> Docker Compose project name (and therefore the `db` container/volume), so the
> test stack must run on its own. If you started the demo stack above, run
> `docker compose down -v` before this step, otherwise the test `db` reuses the
> demo volume (which only has the `ai_export` database) and the smoke/integration
> tests error with `database "ai_export_smoke" does not exist`.

```powershell
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from test
docker compose -f docker-compose.test.yml down -v
```

Expected: **435** unit tests pass and **23** property tests pass on the host; the
containerised suite reports **474 passed, 3 skipped** (the 3 skips are the
live-LLM tests that require a real key).

---

## 10. Cleanup

```powershell
docker compose down -v
```

`-v` removes the PostgreSQL volume so the next demo starts from a clean slate.

---

## Quick reference (copy-paste block)

```powershell
# Start
docker compose up --build -d
docker compose ps

# Health + run
Invoke-RestMethod http://localhost:8000/health
docker compose exec app python -c "from src.pipeline.orchestrator import PipelineOrchestrator; print(PipelineOrchestrator().run('data/sample/leads.csv'))"

# Inspect
Invoke-RestMethod http://localhost:8000/leads
docker compose logs app --tail 40
# Dashboard: http://localhost:8501

# Cleanup
docker compose down -v
```
