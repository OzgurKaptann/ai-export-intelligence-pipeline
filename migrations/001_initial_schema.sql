-- =============================================================================
-- 001_initial_schema.sql
-- AI Export Intelligence Pipeline — initial database schema
--
-- Table creation order respects FK dependencies:
--   1. pipeline_runs          (no FK dependencies)
--   2. raw_leads              (FK → pipeline_runs)
--   3. validated_leads        (FK → pipeline_runs, raw_leads)
--   4. enrichments            (FK → pipeline_runs, validated_leads)
--   5. scored_leads           (FK → pipeline_runs, validated_leads, enrichments)
--   6. data_quality_reports   (FK → pipeline_runs)
--   7. validation_errors      (FK → pipeline_runs, raw_leads)
--
-- All statements use IF NOT EXISTS so the migration is safe to re-run.
-- =============================================================================

-- Required for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- =============================================================================
-- 1. pipeline_runs
-- Tracks each end-to-end pipeline execution.
-- =============================================================================
CREATE TABLE IF NOT EXISTS pipeline_runs (
    pipeline_run_id   UUID          NOT NULL DEFAULT gen_random_uuid(),
    started_at        TIMESTAMPTZ   NOT NULL,
    finished_at       TIMESTAMPTZ,
    status            VARCHAR(32)   NOT NULL DEFAULT 'in_progress',
    processed_count   INTEGER       NOT NULL DEFAULT 0,
    success_count     INTEGER       NOT NULL DEFAULT 0,
    failed_count      INTEGER       NOT NULL DEFAULT 0,
    file_path         TEXT          NOT NULL,
    run_metadata      JSONB,

    CONSTRAINT pk_pipeline_runs
        PRIMARY KEY (pipeline_run_id),

    -- Status must be one of the four lifecycle values.
    CONSTRAINT chk_pipeline_runs_status
        CHECK (status IN ('in_progress', 'completed', 'failed', 'partially_completed')),

    -- Counts must never go negative.
    CONSTRAINT chk_pipeline_runs_processed_count
        CHECK (processed_count >= 0),
    CONSTRAINT chk_pipeline_runs_success_count
        CHECK (success_count >= 0),
    CONSTRAINT chk_pipeline_runs_failed_count
        CHECK (failed_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at
    ON pipeline_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status
    ON pipeline_runs (status);


-- =============================================================================
-- 2. raw_leads
-- Stores unique ingested lead records after idempotency resolution.
-- The UNIQUE constraint on idempotency_key is the enforcement mechanism:
-- duplicate rows are never inserted.
-- =============================================================================
CREATE TABLE IF NOT EXISTS raw_leads (
    raw_lead_id       UUID          NOT NULL DEFAULT gen_random_uuid(),
    idempotency_key   VARCHAR(64)   NOT NULL,
    pipeline_run_id   UUID          NOT NULL,
    company_name      TEXT          NOT NULL,
    contact_email     TEXT          NOT NULL,
    contact_phone     TEXT,
    product_category  TEXT          NOT NULL,
    annual_revenue    NUMERIC(15,2),
    target_market     TEXT,
    raw_csv_row       JSONB         NOT NULL,
    ingested_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_raw_leads
        PRIMARY KEY (raw_lead_id),

    CONSTRAINT uq_raw_leads_idempotency_key
        UNIQUE (idempotency_key),

    CONSTRAINT fk_raw_leads_pipeline_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES pipeline_runs (pipeline_run_id)
        ON DELETE CASCADE
);

-- The UNIQUE index created by the constraint covers the lookup; add
-- non-unique indexes for the remaining common query patterns.
CREATE INDEX IF NOT EXISTS idx_raw_leads_pipeline_run_id
    ON raw_leads (pipeline_run_id);

CREATE INDEX IF NOT EXISTS idx_raw_leads_ingested_at
    ON raw_leads (ingested_at);


-- =============================================================================
-- 3. validated_leads
-- Stores leads that passed RawLeadSchema Pydantic validation.
-- =============================================================================
CREATE TABLE IF NOT EXISTS validated_leads (
    validated_lead_id UUID          NOT NULL DEFAULT gen_random_uuid(),
    raw_lead_id       UUID          NOT NULL,
    pipeline_run_id   UUID          NOT NULL,
    company_name      TEXT          NOT NULL,
    contact_email     TEXT          NOT NULL,
    contact_phone     TEXT,
    product_category  TEXT          NOT NULL,
    annual_revenue    NUMERIC(15,2),
    target_market     TEXT,
    validated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_validated_leads
        PRIMARY KEY (validated_lead_id),

    CONSTRAINT fk_validated_leads_raw_lead
        FOREIGN KEY (raw_lead_id)
        REFERENCES raw_leads (raw_lead_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_validated_leads_pipeline_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES pipeline_runs (pipeline_run_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_validated_leads_raw_lead_id
    ON validated_leads (raw_lead_id);

CREATE INDEX IF NOT EXISTS idx_validated_leads_pipeline_run_id
    ON validated_leads (pipeline_run_id);


-- =============================================================================
-- 4. enrichments
-- LLM enrichment results for every validated lead, including failure details.
-- enrichment_status covers all nine taxonomy values defined in the design.
-- =============================================================================
CREATE TABLE IF NOT EXISTS enrichments (
    enrichment_id         UUID          NOT NULL DEFAULT gen_random_uuid(),
    validated_lead_id     UUID          NOT NULL,
    pipeline_run_id       UUID          NOT NULL,
    enrichment_status     VARCHAR(32)   NOT NULL,
    market_potential      NUMERIC(4,3),
    export_readiness      NUMERIC(4,3),
    risk_assessment       JSONB,
    recommended_markets   TEXT[],
    confidence_score      NUMERIC(4,3),
    error_type            VARCHAR(32),
    error_message         TEXT,
    failed_at             TIMESTAMPTZ,
    retry_count           SMALLINT      NOT NULL DEFAULT 0,
    raw_llm_response      TEXT,
    prompt_version        VARCHAR(32)   NOT NULL,
    model_name            VARCHAR(128)  NOT NULL,
    enrichment_created_at TIMESTAMPTZ,

    CONSTRAINT pk_enrichments
        PRIMARY KEY (enrichment_id),

    CONSTRAINT fk_enrichments_validated_lead
        FOREIGN KEY (validated_lead_id)
        REFERENCES validated_leads (validated_lead_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_enrichments_pipeline_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES pipeline_runs (pipeline_run_id)
        ON DELETE CASCADE,

    -- Enforce the failure taxonomy defined in the design.
    CONSTRAINT chk_enrichments_status
        CHECK (enrichment_status IN (
            'success',
            'validation_failed',
            'timeout',
            'network_error',
            'rate_limited',
            'empty_response',
            'invalid_json',
            'context_retrieval_failed',
            'unknown_error'
        )),

    -- Numeric range constraints (NULL = not yet set / failed before scoring).
    CONSTRAINT chk_enrichments_market_potential
        CHECK (market_potential IS NULL OR (market_potential >= 0 AND market_potential <= 1)),
    CONSTRAINT chk_enrichments_export_readiness
        CHECK (export_readiness IS NULL OR (export_readiness >= 0 AND export_readiness <= 1)),
    CONSTRAINT chk_enrichments_confidence_score
        CHECK (confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)),

    CONSTRAINT chk_enrichments_retry_count
        CHECK (retry_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_enrichments_validated_lead_id
    ON enrichments (validated_lead_id);

CREATE INDEX IF NOT EXISTS idx_enrichments_pipeline_run_id
    ON enrichments (pipeline_run_id);

CREATE INDEX IF NOT EXISTS idx_enrichments_status
    ON enrichments (enrichment_status);

CREATE INDEX IF NOT EXISTS idx_enrichments_prompt_version
    ON enrichments (prompt_version);

CREATE INDEX IF NOT EXISTS idx_enrichments_model_name
    ON enrichments (model_name);


-- =============================================================================
-- 5. scored_leads
-- Final table combining validated lead + enrichment with calculated score.
-- company_name and product_category are denormalised for query performance.
-- =============================================================================
CREATE TABLE IF NOT EXISTS scored_leads (
    scored_lead_id    UUID          NOT NULL DEFAULT gen_random_uuid(),
    validated_lead_id UUID          NOT NULL,
    enrichment_id     UUID          NOT NULL,
    pipeline_run_id   UUID          NOT NULL,
    company_name      TEXT          NOT NULL,
    product_category  TEXT          NOT NULL,
    score             NUMERIC(5,2)  NOT NULL,
    score_breakdown   JSONB         NOT NULL,
    scored_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_scored_leads
        PRIMARY KEY (scored_lead_id),

    CONSTRAINT fk_scored_leads_validated_lead
        FOREIGN KEY (validated_lead_id)
        REFERENCES validated_leads (validated_lead_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_scored_leads_enrichment
        FOREIGN KEY (enrichment_id)
        REFERENCES enrichments (enrichment_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_scored_leads_pipeline_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES pipeline_runs (pipeline_run_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_scored_leads_score
        CHECK (score >= 0 AND score <= 100)
);

CREATE INDEX IF NOT EXISTS idx_scored_leads_validated_lead_id
    ON scored_leads (validated_lead_id);

CREATE INDEX IF NOT EXISTS idx_scored_leads_enrichment_id
    ON scored_leads (enrichment_id);

CREATE INDEX IF NOT EXISTS idx_scored_leads_pipeline_run_id
    ON scored_leads (pipeline_run_id);

CREATE INDEX IF NOT EXISTS idx_scored_leads_score_desc
    ON scored_leads (score DESC);


-- =============================================================================
-- 6. data_quality_reports
-- One report per pipeline run, storing counts of every stage outcome.
-- =============================================================================
CREATE TABLE IF NOT EXISTS data_quality_reports (
    report_id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    pipeline_run_id      UUID        NOT NULL,
    total_records        INTEGER     NOT NULL,
    valid_records        INTEGER     NOT NULL,
    invalid_records      INTEGER     NOT NULL,
    enriched_records     INTEGER     NOT NULL,
    failed_enrichments   INTEGER     NOT NULL,
    scored_records       INTEGER     NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_data_quality_reports
        PRIMARY KEY (report_id),

    -- At most one report per pipeline run.
    CONSTRAINT uq_data_quality_reports_pipeline_run
        UNIQUE (pipeline_run_id),

    CONSTRAINT fk_data_quality_reports_pipeline_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES pipeline_runs (pipeline_run_id)
        ON DELETE CASCADE,

    -- All counts must be non-negative.
    CONSTRAINT chk_dqr_total_records       CHECK (total_records >= 0),
    CONSTRAINT chk_dqr_valid_records       CHECK (valid_records >= 0),
    CONSTRAINT chk_dqr_invalid_records     CHECK (invalid_records >= 0),
    CONSTRAINT chk_dqr_enriched_records    CHECK (enriched_records >= 0),
    CONSTRAINT chk_dqr_failed_enrichments  CHECK (failed_enrichments >= 0),
    CONSTRAINT chk_dqr_scored_records      CHECK (scored_records >= 0)
);

-- The UNIQUE constraint creates an index on pipeline_run_id automatically;
-- no additional index is needed.


-- =============================================================================
-- 7. validation_errors
-- Per-field validation failures recorded during ingestion and enrichment.
-- raw_lead_id is nullable because some errors occur before raw_leads insert.
-- =============================================================================
CREATE TABLE IF NOT EXISTS validation_errors (
    error_id        UUID         NOT NULL DEFAULT gen_random_uuid(),
    pipeline_run_id UUID         NOT NULL,
    raw_lead_id     UUID,
    error_stage     VARCHAR(32)  NOT NULL,
    error_field     VARCHAR(128),
    error_message   TEXT         NOT NULL,
    recorded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_validation_errors
        PRIMARY KEY (error_id),

    CONSTRAINT fk_validation_errors_pipeline_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES pipeline_runs (pipeline_run_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_validation_errors_raw_lead
        FOREIGN KEY (raw_lead_id)
        REFERENCES raw_leads (raw_lead_id)
        ON DELETE SET NULL,

    CONSTRAINT chk_validation_errors_stage
        CHECK (error_stage IN ('ingestion', 'validation', 'enrichment', 'scoring'))
);

CREATE INDEX IF NOT EXISTS idx_validation_errors_pipeline_run_id
    ON validation_errors (pipeline_run_id);

CREATE INDEX IF NOT EXISTS idx_validation_errors_error_stage
    ON validation_errors (error_stage);

CREATE INDEX IF NOT EXISTS idx_validation_errors_recorded_at
    ON validation_errors (recorded_at DESC);
