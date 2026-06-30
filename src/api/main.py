"""
FastAPI application scaffold for the AI Export Intelligence Pipeline (Task 19).

This module exposes the importable ASGI app as ``src.api.main:app`` so the
service can be started with::

    uvicorn src.api.main:app --reload

Design constraints (kept deliberately small for the scaffold):

* No configuration is read at import time — ``get_settings()`` is never called
  while this module loads, so the app imports without ``DATABASE_URL``.
* No database engine or session is created at import time; the database
  dependency (:func:`get_db`) is only re-exported here and is resolved lazily
  per-request by FastAPI's ``Depends``.
* ``/health`` is minimal and deterministic — it performs no database query and
  needs no environment variables or API keys.

Business routes for leads (Task 20), pipeline runs (Task 21) and the dashboard
(Task 22) are intentionally **not** wired up here yet.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Re-export the existing database session dependency so route modules added in
# later tasks can simply ``from src.api.main import get_db`` (or import it from
# its canonical location).  Importing it here does NOT open a connection — the
# engine/session are built lazily on first request.
from src.database.session import get_db  # noqa: F401  (re-exported dependency)

# Local development origins for the Streamlit dashboard (8501) and a generic
# front-end dev server (3000).  Kept simple and explicit for local use.
CORS_ALLOW_ORIGINS = [
    "http://localhost:8501",
    "http://127.0.0.1:8501",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context manager.

    Startup/shutdown hooks live here.  The scaffold has nothing to initialise
    yet (no connection pools are opened eagerly), so this simply yields control
    to the running application.
    """
    # --- startup ---
    yield
    # --- shutdown ---


app = FastAPI(
    title="AI Export Intelligence Pipeline API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe.

    Deterministic and dependency-free: always returns ``{"status": "ok"}``
    without touching the database or any external service.
    """
    return {"status": "ok"}
