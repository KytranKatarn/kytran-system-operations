"""
Postgres schema migrations for the standalone.

Run at app startup via run_migrations() — idempotent, safe to re-run.
Each .sql file in this directory is executed once; applied migrations
are tracked in the schema_migrations table.
"""

import glob
import logging
import os
import time

logger = logging.getLogger(__name__)


_MIGRATIONS_DIR = os.path.dirname(os.path.abspath(__file__))


def run_migrations(max_wait_seconds=60):
    """Apply all pending SQL migrations in order.

    Waits for Postgres to be reachable (up to max_wait_seconds) before
    applying. Safe to call every startup.
    """
    from database import get_db_cursor, _get_pool

    # Wait for Postgres to accept connections
    deadline = time.time() + max_wait_seconds
    last_err = None
    while time.time() < deadline:
        try:
            _get_pool()
            break
        except Exception as e:
            last_err = e
            time.sleep(2)
    else:
        raise RuntimeError(f"Postgres not reachable within {max_wait_seconds}s: {last_err}")

    # Create the tracking table
    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename VARCHAR(255) PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    # Discover SQL files in this directory, ordered by name
    sql_files = sorted(glob.glob(os.path.join(_MIGRATIONS_DIR, "*.sql")))
    if not sql_files:
        logger.info("No migration .sql files found in %s", _MIGRATIONS_DIR)
        return

    with get_db_cursor() as (conn, cur):
        cur.execute("SELECT filename FROM schema_migrations")
        applied = {row["filename"] for row in cur.fetchall()}

    applied_count = 0
    for path in sql_files:
        name = os.path.basename(path)
        if name in applied:
            continue
        logger.info("Applying migration %s", name)
        with open(path, "r") as f:
            sql = f.read()
        with get_db_cursor() as (conn, cur):
            cur.execute(sql)
            cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (name,))
        applied_count += 1

    if applied_count:
        logger.info("Applied %d migration(s)", applied_count)
    else:
        logger.info("No pending migrations")
