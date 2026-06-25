"""
Database session management for the AI Export Intelligence Pipeline.

Provides:
- ``get_engine``  — create a SQLAlchemy Engine for a given database URL
- ``SessionLocal`` — module-level sessionmaker bound to the app's default DB
- ``get_db``       — FastAPI-compatible generator dependency
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def get_engine(database_url: str) -> Engine:
    """Create and return a SQLAlchemy engine for *database_url*.

    ``pool_pre_ping=True`` ensures stale connections are detected and
    recycled before use, which is important for long-lived server processes.

    Args:
        database_url: A valid SQLAlchemy database URL string, e.g.
            ``"postgresql://user:pass@localhost:5432/mydb"`` or
            ``"sqlite:///:memory:"`` for tests.

    Returns:
        A configured :class:`sqlalchemy.engine.Engine` instance.
    """
    return create_engine(database_url, pool_pre_ping=True)


def _build_session_local() -> sessionmaker[Session]:
    """Build the module-level sessionmaker from application settings.

    Deferred import of ``get_settings`` so that the module can be imported
    in test environments where DATABASE_URL may not be set (tests supply
    their own engine instead of using ``SessionLocal``).
    """
    from src.config import get_settings  # local import to avoid circular deps

    settings = get_settings()
    engine = get_engine(settings.DATABASE_URL)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Module-level sessionmaker — lazily built when first accessed.
# Tests should NOT rely on this; they create their own engine via get_engine().
SessionLocal: sessionmaker[Session] = None  # type: ignore[assignment]


def _get_session_local() -> sessionmaker[Session]:
    """Return (and lazily initialise) the module-level SessionLocal."""
    global SessionLocal
    if SessionLocal is None:
        SessionLocal = _build_session_local()
    return SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session.

    Usage::

        @router.get("/items")
        def list_items(db: Session = Depends(get_db)):
            ...

    The session is always closed in the ``finally`` block, whether or not an
    exception was raised inside the request handler.

    Yields:
        An open :class:`sqlalchemy.orm.Session` bound to the application DB.
    """
    db: Session = _get_session_local()()
    try:
        yield db
    finally:
        db.close()
