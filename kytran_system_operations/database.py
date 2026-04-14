"""
Database Connection Helper for Kytran System Operations standalone.

This shim mirrors the platform_v2/database.py API so the modules copied
over from ARCHIE's system_operations tool work without any rewrites.
It connects to a Postgres sidecar container (see docker-compose.yml).

Contract (mirrors platform):
    from database import get_db, get_db_cursor, release_db, _get_db_password
    with get_db_cursor() as (conn, cur):
        cur.execute("SELECT * FROM table")
        rows = cur.fetchall()
"""

from contextlib import contextmanager
import atexit
import os
import threading

from psycopg2 import pool
from psycopg2.extras import RealDictCursor


_pool = None
_pool_lock = threading.Lock()


def _get_db_password():
    """Read DB password from Docker secret file, fall back to env var."""
    secret_file = os.environ.get("DB_PASSWORD_FILE", "/run/secrets/db_password")
    try:
        with open(secret_file, "r") as f:
            pw = f.read().strip()
            if pw:
                return pw
    except (FileNotFoundError, PermissionError):
        pass
    return os.environ.get("DB_PASSWORD", "")


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=20,
                    host=os.environ.get("DB_HOST", "postgres"),
                    port=os.environ.get("DB_PORT", "5432"),
                    database=os.environ.get("DB_NAME", "sysops"),
                    user=os.environ.get("DB_USER", "sysops"),
                    password=_get_db_password(),
                )
    return _pool


def _cleanup_pool():
    global _pool
    if _pool is not None:
        _pool.closeall()


atexit.register(_cleanup_pool)


class _PooledConnection:
    """Wrapper that returns connection to pool on close() instead of destroying it."""

    __slots__ = ("_conn", "_pool", "_released")

    def __init__(self, conn, pool_ref):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_pool", pool_ref)
        object.__setattr__(self, "_released", False)

    def close(self):
        if not self._released:
            object.__setattr__(self, "_released", True)
            try:
                self._pool.putconn(self._conn)
            except Exception:
                pass

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def get_db():
    """Get a pooled Postgres connection. Prefer get_db_cursor() when possible."""
    p = _get_pool()
    return _PooledConnection(p.getconn(), p)


def release_db(conn):
    if conn is not None:
        try:
            if isinstance(conn, _PooledConnection):
                conn.close()
            else:
                _get_pool().putconn(conn)
        except Exception:
            pass


@contextmanager
def get_db_connection(cursor_factory=None):
    conn = None
    try:
        conn = get_db()
        if cursor_factory:
            conn.cursor_factory = cursor_factory
        yield conn
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn is not None:
            conn.close()


@contextmanager
def get_db_cursor(cursor_factory=RealDictCursor, commit=True):
    """Context manager yielding (connection, cursor). Auto-commits on success."""
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=cursor_factory)
        yield conn, cur
        if commit:
            conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            conn.close()
