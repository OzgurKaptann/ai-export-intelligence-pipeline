"""
Unit tests for the FastAPI application scaffold (Task 19).

These tests verify the scaffold contract in complete isolation:

* No live database — ``/health`` never queries the DB, and importing the app
  does not open a connection or build the module-level ``SessionLocal``.
* No ``DATABASE_URL`` is required to import the app or hit ``/health``.
* No OpenAI, no network and no ``OPENAI_API_KEY``.
* The scaffold routes plus the wired-up business routers are present — the
  ``/leads`` (Task 20) and ``/pipeline-runs`` (Task 21) routes are registered.
"""

from __future__ import annotations

import subprocess
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import src.api.main as api_main
from src.api.main import app
from src.database.session import get_db as session_get_db

client = TestClient(app)


# ---------------------------------------------------------------------------
# App object
# ---------------------------------------------------------------------------

def test_app_imports_successfully():
    # Reaching this point means ``import src.api.main`` succeeded at module load.
    assert api_main.app is not None


def test_app_is_fastapi_instance():
    assert isinstance(app, FastAPI)


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------

def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_status_ok():
    response = client.get("/health")
    assert response.json() == {"status": "ok"}


def test_health_does_not_require_database_url(monkeypatch):
    # /health is deterministic and dependency-free: it must work even with no
    # DATABASE_URL in the environment.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_does_not_require_openai_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

def test_app_includes_cors_middleware():
    middleware_classes = [m.cls for m in app.user_middleware]
    assert CORSMiddleware in middleware_classes


def test_cors_allows_local_dashboard_origin():
    origin = "http://localhost:8501"
    response = client.get("/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


# ---------------------------------------------------------------------------
# Database dependency
# ---------------------------------------------------------------------------

def test_database_dependency_is_importable():
    # The scaffold re-exports the existing get_db from src.database.session.
    assert hasattr(api_main, "get_db")
    assert callable(api_main.get_db)


def test_database_dependency_forwards_session_get_db():
    # It must be the same object, not a re-implementation.
    assert api_main.get_db is session_get_db


# ---------------------------------------------------------------------------
# No import-time database connection
# ---------------------------------------------------------------------------

def test_import_does_not_open_database_connection():
    # Importing the app must not build the module-level SessionLocal, which is
    # what triggers get_settings() + engine creation.  Verified in a clean
    # subprocess with DATABASE_URL and OPENAI_API_KEY removed so the result is
    # independent of any state other tests may have created.
    code = (
        "import os\n"
        "os.environ.pop('DATABASE_URL', None)\n"
        "os.environ.pop('OPENAI_API_KEY', None)\n"
        "import src.api.main\n"
        "import src.database.session as s\n"
        "assert s.SessionLocal is None, 'SessionLocal built at import time'\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# ---------------------------------------------------------------------------
# Registered routes
# ---------------------------------------------------------------------------

def test_health_and_leads_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    # The health route is present...
    assert "/health" in paths
    # ...and Task 20 intentionally adds the /leads routes.
    assert any(p.startswith("/leads") for p in paths), "leads routes missing"


def test_pipeline_runs_routes_registered():
    # Task 21 intentionally adds the /pipeline-runs routes.
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert any(p.startswith("/pipeline-runs") for p in paths), (
        "pipeline-runs routes missing"
    )
