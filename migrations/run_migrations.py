"""
migrations/run_migrations.py
============================
Applies all SQL migration files in sorted order against the configured
PostgreSQL database.

Usage
-----
    # Uses DATABASE_URL from environment or .env file
    python migrations/run_migrations.py

Exit codes
----------
    0  — all migrations applied successfully (or already applied)
    1  — one or more migrations failed; database state is unchanged for
         the failing file (psycopg2 rolls back on error)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Make sure the project root is importable so src/config.py can be loaded.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _normalize_for_psycopg2(url: str) -> str:
    """
    Strip a SQLAlchemy driver suffix from the URL scheme for libpq/psycopg2.

    SQLAlchemy URLs use ``postgresql+psycopg2://...`` but ``psycopg2.connect``
    (libpq) only understands ``postgresql://`` / ``postgres://``.  Drop the
    ``+driver`` part so the same DATABASE_URL works for both SQLAlchemy (the
    app) and psycopg2 (this migration script).  Plain URLs pass through
    unchanged.
    """
    for prefix in ("postgresql+", "postgres+"):
        if url.startswith(prefix):
            base, _, rest = url[len(prefix):].partition("://")
            # ``base`` is the driver name (e.g. "psycopg2"); discard it.
            return f"{prefix[:-1]}://{rest}"
    return url


def _get_database_url() -> str:
    """
    Resolve DATABASE_URL from the environment.

    1. Try the raw environment variable first (works in Docker / CI).
    2. Fall back to src.config.Settings, which also reads .env.

    The resolved URL is normalized for psycopg2 (any ``+driver`` suffix in the
    scheme is removed).
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return _normalize_for_psycopg2(url)

    try:
        from src.config import Settings
        return _normalize_for_psycopg2(Settings().DATABASE_URL)
    except Exception as exc:
        print(
            f"[migrations] ERROR: could not resolve DATABASE_URL.\n"
            f"  Set DATABASE_URL as an environment variable or in a .env file.\n"
            f"  Details: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


def run_migrations(migrations_dir: Path | None = None) -> None:
    """
    Apply all *.sql files in *migrations_dir* in lexicographic order.

    Each file is executed in its own transaction (autocommit=False means
    psycopg2 wraps it in an implicit BEGIN/COMMIT).  If a file raises an
    error the transaction is rolled back and the script exits with code 1.
    """
    try:
        import psycopg2
    except ImportError:
        print(
            "[migrations] ERROR: psycopg2 is not installed.\n"
            "  Run: pip install psycopg2-binary",
            file=sys.stderr,
        )
        sys.exit(1)

    if migrations_dir is None:
        migrations_dir = Path(__file__).resolve().parent

    sql_files = sorted(migrations_dir.glob("*.sql"))

    if not sql_files:
        print("[migrations] No .sql files found — nothing to apply.")
        return

    database_url = _get_database_url()

    print(f"[migrations] Connecting to database ...")
    try:
        conn = psycopg2.connect(database_url)
    except psycopg2.OperationalError as exc:
        print(f"[migrations] ERROR: could not connect to database.\n  {exc}", file=sys.stderr)
        sys.exit(1)

    conn.autocommit = False
    cursor = conn.cursor()

    for sql_path in sql_files:
        print(f"[migrations] Applying {sql_path.name} ...", end=" ", flush=True)
        sql = sql_path.read_text(encoding="utf-8")
        try:
            cursor.execute(sql)
            conn.commit()
            print("OK")
        except Exception as exc:
            conn.rollback()
            print(f"FAILED\n[migrations] ERROR in {sql_path.name}:\n  {exc}", file=sys.stderr)
            cursor.close()
            conn.close()
            sys.exit(1)

    cursor.close()
    conn.close()
    print(f"[migrations] All {len(sql_files)} migration(s) applied successfully.")


if __name__ == "__main__":
    run_migrations()
