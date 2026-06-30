# =============================================================================
# Dockerfile — FastAPI application container (Task 25)
#
# Builds the API image: installs the pinned Python dependencies, copies only the
# code the service needs (src/, migrations/, data/), applies the database
# migration and then starts uvicorn.
#
# Secrets are NEVER baked into the image — DATABASE_URL, OPENAI_API_KEY, etc. are
# provided at runtime via docker-compose `environment:`.
# =============================================================================
FROM python:3.11-slim

# Predictable, log-friendly Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so the layer is cached when only source changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the app needs to run.
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY data/ ./data/

EXPOSE 8000

# Run the migration first, then start the API. Shell form is used so both
# commands run in sequence and uvicorn only starts once migrations succeed.
CMD python migrations/run_migrations.py && \
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000
