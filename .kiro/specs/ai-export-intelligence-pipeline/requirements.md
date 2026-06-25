# Requirements Document

## Introduction

The AI Export Intelligence Pipeline is a production-grade demonstration system that ingests synthetic export lead and company data, enriches it using LLM-based analysis with structured JSON outputs, calculates lead scores, and stores results in PostgreSQL. The system exposes enriched data through a FastAPI endpoint and provides dashboard visualization capabilities. This project demonstrates data engineering, AI pipeline design, schema validation, and clean architecture practices suitable for portfolio presentation.

## Glossary

- **Pipeline**: The AI Export Intelligence Pipeline system
- **Lead**: A potential export opportunity containing company and product information
- **CSV_Ingestion_Module**: Component that reads and validates raw CSV files
- **Schema_Validator**: Component that validates JSON structures against defined schemas
- **LLM_Enrichment_Module**: Component that uses a Large Language Model to analyze and enrich lead data
- **Knowledge_Base**: Optional repository of product and export domain information used for context retrieval
- **Lead_Scorer**: Component that calculates numerical scores for leads based on enrichment data
- **Database_Layer**: PostgreSQL storage component managing raw, processed, enriched, and scored data
- **API_Layer**: FastAPI component exposing lead data through REST endpoints
- **Dashboard**: Visualization component for displaying lead intelligence
- **Enrichment_Output**: Structured JSON data produced by LLM analysis
- **Test_Suite**: Automated validation and testing components
- **Pipeline_Run**: A single end-to-end execution of the Pipeline, identified by a unique `pipeline_run_id` and tracked from start to finish
- **Enrichment_Status**: A categorical field describing the outcome of an LLM enrichment attempt for a single lead; valid values are `success`, `validation_failed`, `timeout`, `network_error`, `rate_limited`, `empty_response`, `invalid_json`, `context_retrieval_failed`, and `unknown_error`
- **Retry_Policy**: The set of rules that determine which error types are eligible for retry, the maximum number of retry attempts, and back-off behaviour
- **Idempotency_Key**: A stable, deterministic identifier derived from a lead record used to detect and handle duplicate processing attempts
- **Data_Quality_Report**: A summary artifact produced at the end of each Pipeline_Run that tallies total, valid, invalid, enriched, failed, and scored record counts

## Requirements

### Requirement 1: Data Ingestion

**User Story:** As a data engineer, I want to ingest synthetic export lead data from CSV files, so that I can process and analyze potential export opportunities.

#### Acceptance Criteria

1. WHEN a CSV file is provided, THE CSV_Ingestion_Module SHALL parse the file and extract lead records
2. WHEN a CSV file contains invalid formatting, THE CSV_Ingestion_Module SHALL return a descriptive error message
3. THE CSV_Ingestion_Module SHALL validate that required fields (company name, contact information, product category) are present in each record
4. WHEN ingestion is successful, THE CSV_Ingestion_Module SHALL store raw data in the Database_Layer
5. THE Pipeline SHALL ensure all ingested data is synthetic and contains no confidential information

### Requirement 2: Input Schema Validation

**User Story:** As a data engineer, I want to validate input data schemas, so that I can ensure data quality before processing.

#### Acceptance Criteria

1. WHEN raw lead data is received, THE Schema_Validator SHALL validate the data structure against the defined input schema
2. WHEN validation fails, THE Schema_Validator SHALL return specific error messages indicating which fields are invalid
3. WHEN validation succeeds, THE Schema_Validator SHALL mark the record as validated in the Database_Layer
4. THE Schema_Validator SHALL enforce data type constraints for all fields
5. THE Schema_Validator SHALL reject records with missing required fields

### Requirement 3: LLM-Based Lead Enrichment

**User Story:** As an export analyst, I want to enrich lead data using AI analysis, so that I can gain insights about export opportunities.

#### Acceptance Criteria

1. WHEN a validated lead is ready for enrichment, THE LLM_Enrichment_Module SHALL send the lead data to the LLM with a structured prompt
2. THE LLM_Enrichment_Module SHALL receive structured JSON output from the LLM
3. WHEN LLM output is received, THE Schema_Validator SHALL validate the output against the enrichment schema before storage
4. IF LLM output validation fails, THEN THE Pipeline SHALL log the error and mark the record as failed enrichment
5. WHEN enrichment succeeds, THE LLM_Enrichment_Module SHALL store the Enrichment_Output in the Database_Layer
6. THE LLM_Enrichment_Module SHALL include market potential, export readiness, and risk assessment in the Enrichment_Output

### Requirement 4: Knowledge Base Context Retrieval

**User Story:** As an export analyst, I want to optionally retrieve relevant context from a knowledge base, so that the LLM has domain-specific information for enrichment.

#### Acceptance Criteria

1. WHERE knowledge base functionality is enabled, WHEN a lead is being enriched, THE LLM_Enrichment_Module SHALL query the Knowledge_Base for relevant product and export information
2. WHERE knowledge base functionality is enabled, THE LLM_Enrichment_Module SHALL include retrieved context in the LLM prompt
3. WHERE knowledge base functionality is disabled, THE LLM_Enrichment_Module SHALL perform enrichment without external context
4. WHEN Knowledge_Base queries fail, THE LLM_Enrichment_Module SHALL proceed with enrichment using only lead data

### Requirement 5: Lead Scoring

**User Story:** As an export manager, I want leads to be automatically scored, so that I can prioritize high-potential opportunities.

#### Acceptance Criteria

1. WHEN enrichment data is available, THE Lead_Scorer SHALL calculate a numerical score between 0 and 100
2. THE Lead_Scorer SHALL base the score on market potential, export readiness, and risk factors from the Enrichment_Output
3. WHEN scoring is complete, THE Lead_Scorer SHALL store the score in the Database_Layer
4. THE Lead_Scorer SHALL handle missing enrichment fields by applying default scoring logic
5. WHEN Enrichment_Output contains invalid data types, THE Lead_Scorer SHALL log an error and assign a default score

### Requirement 6: Multi-Stage Data Storage

**User Story:** As a data engineer, I want to store data at multiple pipeline stages, so that I can audit and debug the pipeline.

#### Acceptance Criteria

1. THE Database_Layer SHALL store raw lead data in a dedicated table
2. THE Database_Layer SHALL store validated and processed lead data in a dedicated table
3. THE Database_Layer SHALL store enrichment results in a dedicated table with fields for `enrichment_status`, `error_type`, `error_message`, `failed_at`, `retry_count`, `raw_llm_response`, `prompt_version`, `model_name`, and `enrichment_created_at`
4. THE Database_Layer SHALL store final scored leads in a dedicated table
5. THE Database_Layer SHALL maintain foreign key relationships between pipeline stages
6. WHEN data is inserted at any stage, THE Database_Layer SHALL record timestamps
7. THE Database_Layer SHALL use PostgreSQL as the storage engine
8. THE Database_Layer SHALL store Pipeline_Run metadata including `pipeline_run_id`, `started_at`, `finished_at`, `status`, `processed_count`, `success_count`, and `failed_count` in a dedicated table

### Requirement 7: REST API Exposure

**User Story:** As a developer, I want to access lead data through a REST API, so that I can integrate with other systems.

#### Acceptance Criteria

1. THE API_Layer SHALL expose an endpoint to retrieve all scored leads
2. THE API_Layer SHALL expose an endpoint to retrieve a single lead by identifier
3. THE API_Layer SHALL expose an endpoint to filter leads by score threshold
4. WHEN an API request is received, THE API_Layer SHALL return data in JSON format
5. WHEN an invalid lead identifier is requested, THE API_Layer SHALL return a 404 status code with an error message
6. THE API_Layer SHALL use FastAPI as the web framework
7. THE API_Layer SHALL include API documentation endpoints

### Requirement 8: Dashboard Visualization

**User Story:** As an export manager, I want to view lead intelligence through a dashboard, so that I can quickly assess opportunities.

#### Acceptance Criteria

1. THE Dashboard SHALL display a list of scored leads sorted by score
2. THE Dashboard SHALL display lead enrichment details for individual leads
3. THE Dashboard SHALL display summary statistics including total leads, average score, and score distribution
4. WHEN lead data is updated, THE Dashboard SHALL reflect the updated information
5. THE Dashboard SHALL provide filtering capabilities by score range and export readiness

### Requirement 9: Structured JSON Validation

**User Story:** As a data engineer, I want to validate all AI outputs before database insertion, so that I can prevent data corruption.

#### Acceptance Criteria

1. THE Schema_Validator SHALL define JSON schemas for all LLM output structures
2. WHEN LLM output is received, THE Schema_Validator SHALL validate the JSON structure before any database operation
3. IF validation fails, THEN THE Schema_Validator SHALL prevent database insertion and log validation errors
4. THE Schema_Validator SHALL validate field presence, data types, and value constraints
5. FOR ALL enrichment operations, THE Pipeline SHALL ensure that only validated JSON is stored in the Database_Layer

### Requirement 10: Docker Containerization

**User Story:** As a DevOps engineer, I want the entire pipeline containerized, so that I can deploy it consistently across environments.

#### Acceptance Criteria

1. THE Pipeline SHALL provide a Dockerfile for the application container
2. THE Pipeline SHALL provide a docker-compose configuration for multi-container orchestration
3. WHEN docker-compose is executed, THE Pipeline SHALL start the application, database, and dashboard containers
4. THE Pipeline SHALL ensure PostgreSQL data persists across container restarts
5. THE Pipeline SHALL expose appropriate ports for API and dashboard access

### Requirement 11: Testing and Validation

**User Story:** As a developer, I want automated tests for all pipeline components, so that I can ensure system reliability.

#### Acceptance Criteria

1. THE Test_Suite SHALL include unit tests for schema validation logic
2. THE Test_Suite SHALL include integration tests for the complete pipeline flow
3. THE Test_Suite SHALL include API endpoint tests for all exposed routes
4. THE Test_Suite SHALL validate that LLM enrichment produces valid JSON structures
5. WHEN tests are executed, THE Test_Suite SHALL report pass/fail status for each test
6. THE Test_Suite SHALL use synthetic test data only

### Requirement 12: Documentation

**User Story:** As a portfolio reviewer, I want comprehensive documentation, so that I can understand the project architecture and purpose.

#### Acceptance Criteria

1. THE Pipeline SHALL include a README file describing project purpose, architecture, and setup instructions
2. THE Pipeline SHALL include architecture diagrams showing data flow through pipeline stages
3. THE Pipeline SHALL include API documentation with endpoint descriptions and example requests
4. THE Pipeline SHALL include a section explaining technology choices and design decisions
5. THE Pipeline SHALL include instructions for running tests and validating the system
6. THE Pipeline SHALL include sample outputs demonstrating pipeline capabilities
7. THE Pipeline SHALL clearly state that all data is synthetic and suitable for public repositories

### Requirement 13: Configuration Management

**User Story:** As a deployment engineer, I want to configure the pipeline through environment variables, so that I can adapt it to different environments.

#### Acceptance Criteria

1. THE Pipeline SHALL read database connection parameters from environment variables
2. THE Pipeline SHALL read LLM API configuration from environment variables
3. THE Pipeline SHALL read feature flags (such as knowledge base enable/disable) from environment variables
4. WHERE environment variables are not set, THE Pipeline SHALL use sensible default values
5. THE Pipeline SHALL provide a sample environment configuration file in the repository

### Requirement 14: Error Handling and Logging

**User Story:** As a system operator, I want comprehensive error handling and logging, so that I can diagnose and resolve issues.

#### Acceptance Criteria

1. WHEN an error occurs at any pipeline stage, THE Pipeline SHALL log the error with timestamp, stage identifier, and error details
2. WHEN a lead fails enrichment, THE Pipeline SHALL store the failure reason in the Database_Layer with `enrichment_status`, `error_type`, `error_message`, `failed_at`, and `retry_count`
3. THE Pipeline SHALL continue processing remaining leads after individual lead failures
4. THE Pipeline SHALL provide structured logging output in JSON format
5. WHERE appropriate, THE Pipeline SHALL include error recovery mechanisms with retry logic
6. WHEN an LLM response is invalid or parsing fails, THE Pipeline SHALL store the `raw_llm_response` for audit purposes

### Requirement 15: Pipeline Run Tracking

**User Story:** As a system operator, I want to track each pipeline execution independently, so that I can audit execution history and diagnose performance issues.

#### Acceptance Criteria

1. WHEN the Pipeline starts execution, THE Pipeline SHALL generate a unique `pipeline_run_id`
2. THE Pipeline SHALL record `started_at` timestamp at the beginning of each Pipeline_Run
3. THE Pipeline SHALL record `finished_at` timestamp at the end of each Pipeline_Run
4. THE Pipeline SHALL track `status` for each Pipeline_Run with values including `in_progress`, `completed`, `failed`, and `partially_completed`
5. THE Pipeline SHALL accumulate `processed_count`, `success_count`, and `failed_count` during each Pipeline_Run
6. THE Pipeline SHALL store all Pipeline_Run metadata in the Database_Layer
7. WHEN a Pipeline_Run completes, THE Pipeline SHALL update the status and final counts in the Database_Layer

### Requirement 16: Enrichment Failure Taxonomy

**User Story:** As a data engineer, I want to classify enrichment failures into specific categories, so that I can identify systemic issues and improve reliability.

#### Acceptance Criteria

1. WHEN enrichment succeeds, THE LLM_Enrichment_Module SHALL set `enrichment_status` to `success`
2. WHEN enrichment output fails schema validation, THE LLM_Enrichment_Module SHALL set `enrichment_status` to `validation_failed`
3. WHEN enrichment exceeds the configured time limit, THE LLM_Enrichment_Module SHALL set `enrichment_status` to `timeout`
4. WHEN a network connection fails during enrichment, THE LLM_Enrichment_Module SHALL set `enrichment_status` to `network_error`
5. WHEN the LLM service returns a rate limit response, THE LLM_Enrichment_Module SHALL set `enrichment_status` to `rate_limited`
6. WHEN the LLM returns an empty response, THE LLM_Enrichment_Module SHALL set `enrichment_status` to `empty_response`
7. WHEN the LLM response cannot be parsed as valid JSON, THE LLM_Enrichment_Module SHALL set `enrichment_status` to `invalid_json`
8. WHEN Knowledge_Base context retrieval fails and enrichment cannot proceed, THE LLM_Enrichment_Module SHALL set `enrichment_status` to `context_retrieval_failed`
9. WHEN an error does not match any defined category, THE LLM_Enrichment_Module SHALL set `enrichment_status` to `unknown_error`
10. THE LLM_Enrichment_Module SHALL store the `enrichment_status` value in the Database_Layer for every enrichment attempt

### Requirement 17: Error Audit Fields

**User Story:** As a system operator, I want detailed error information stored for every failure, so that I can troubleshoot specific enrichment issues.

#### Acceptance Criteria

1. WHEN enrichment fails, THE LLM_Enrichment_Module SHALL store `error_type` matching the `enrichment_status` category
2. WHEN enrichment fails, THE LLM_Enrichment_Module SHALL store a descriptive `error_message` containing actionable diagnostic information
3. WHEN enrichment fails, THE LLM_Enrichment_Module SHALL store `failed_at` timestamp
4. THE LLM_Enrichment_Module SHALL initialize `retry_count` to 0 for new enrichment attempts
5. WHEN a retry is attempted, THE LLM_Enrichment_Module SHALL increment `retry_count` before the retry operation
6. WHEN the LLM returns a response that fails parsing or validation, THE LLM_Enrichment_Module SHALL store `raw_llm_response` in the Database_Layer
7. WHEN enrichment succeeds, THE LLM_Enrichment_Module SHALL set `error_type`, `error_message`, and `failed_at` to null

### Requirement 18: Retry Policy

**User Story:** As a data engineer, I want automatic retry for transient failures, so that temporary issues do not result in permanent data loss.

#### Acceptance Criteria

1. THE Pipeline SHALL define a Retry_Policy that specifies which `enrichment_status` values are eligible for retry
2. THE Retry_Policy SHALL classify `timeout`, `network_error`, and `rate_limited` as retryable errors
3. THE Retry_Policy SHALL classify `validation_failed`, `empty_response`, `invalid_json`, and `context_retrieval_failed` as non-retryable errors
4. THE Retry_Policy SHALL define a maximum retry count of 3 attempts
5. WHEN a retryable error occurs and `retry_count` is below the maximum, THE LLM_Enrichment_Module SHALL retry the enrichment operation
6. WHEN a retryable error occurs and `retry_count` equals the maximum, THE LLM_Enrichment_Module SHALL mark the record as permanently failed
7. WHEN a non-retryable error occurs, THE LLM_Enrichment_Module SHALL mark the record as permanently failed without retry
8. WHEN a lead enrichment fails, THE Pipeline SHALL continue processing remaining leads without stopping
9. WHERE retry configuration is required, THE Pipeline SHALL read retry parameters from environment variables

### Requirement 19: Prompt and Model Traceability

**User Story:** As an AI engineer, I want to track which prompt version and model were used for each enrichment, so that I can correlate enrichment quality with specific configurations.

#### Acceptance Criteria

1. THE LLM_Enrichment_Module SHALL maintain a `prompt_version` identifier for the enrichment prompt template
2. WHEN a lead is enriched, THE LLM_Enrichment_Module SHALL record the current `prompt_version` used for that enrichment
3. THE LLM_Enrichment_Module SHALL record the `model_name` of the LLM used for enrichment
4. THE LLM_Enrichment_Module SHALL record `enrichment_created_at` timestamp when enrichment completes successfully
5. THE LLM_Enrichment_Module SHALL store `prompt_version`, `model_name`, and `enrichment_created_at` in the Database_Layer
6. WHERE prompt templates are updated, THE Pipeline SHALL increment or update the `prompt_version` identifier
7. THE Database_Layer SHALL enable filtering and analysis of enrichment results by `prompt_version` and `model_name`

### Requirement 20: Idempotency

**User Story:** As a data engineer, I want the pipeline to handle duplicate inputs gracefully, so that reprocessing the same file or lead does not corrupt data or create duplicates.

#### Acceptance Criteria

1. THE Pipeline SHALL generate an Idempotency_Key for each lead based on deterministic lead attributes
2. WHEN a lead is ingested, THE CSV_Ingestion_Module SHALL check if a lead with the same Idempotency_Key already exists in the Database_Layer
3. WHERE a duplicate lead is detected, THE Pipeline SHALL skip processing by default
4. WHERE idempotency behavior is configurable and set to "update", THE Pipeline SHALL update the existing record with new data
5. WHERE idempotency behavior is configurable and set to "reprocess", THE Pipeline SHALL delete the old record and process the new one as a fresh lead
6. WHERE idempotency behavior is not explicitly configured, THE Pipeline SHALL default to "skip" mode
7. THE Pipeline SHALL read idempotency behavior configuration from environment variables
8. WHEN a duplicate lead is skipped, THE Pipeline SHALL log the skip event with the Idempotency_Key
9. THE Database_Layer SHALL enforce uniqueness constraints on Idempotency_Key fields to prevent duplicate insertion

### Requirement 21: Data Quality Reporting

**User Story:** As a product manager, I want a summary report after each pipeline run, so that I can assess data quality and pipeline health.

#### Acceptance Criteria

1. WHEN a Pipeline_Run completes, THE Pipeline SHALL generate a Data_Quality_Report
2. THE Data_Quality_Report SHALL include total record count from the input file
3. THE Data_Quality_Report SHALL include count of valid records that passed schema validation
4. THE Data_Quality_Report SHALL include count of invalid records that failed schema validation
5. THE Data_Quality_Report SHALL include count of successfully enriched records
6. THE Data_Quality_Report SHALL include count of failed enrichment records
7. THE Data_Quality_Report SHALL include count of scored records
8. THE Pipeline SHALL store the Data_Quality_Report in the Database_Layer associated with the `pipeline_run_id`
9. THE API_Layer SHALL expose an endpoint to retrieve Data_Quality_Report by `pipeline_run_id`
10. THE Dashboard SHALL display the Data_Quality_Report summary for recent Pipeline_Run executions
