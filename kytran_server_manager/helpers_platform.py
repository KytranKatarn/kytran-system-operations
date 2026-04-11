"""
System Operations — Shared helpers and constants
=================================================
Extracted from routes.py during ADR-045 route split refactor.
"""

from flask import jsonify, request
from flask_login import current_user
from psycopg2.extras import Json
import psycopg2
import os
from database import _get_db_password
import json as json_lib
import time as time_mod

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_DIR = os.environ.get("ARCHIE_BASE_DIR", "/mnt/archie_brain")
HOST_DATA_FILE = os.path.join(BASE_DIR, "host_monitor_data.json")


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def load_host_monitor_data():
    """Load host monitor data from JSON file. Returns (data, age_seconds) or (None, None)."""
    try:
        if not os.path.exists(HOST_DATA_FILE):
            return None, None
        age = time_mod.time() - os.path.getmtime(HOST_DATA_FILE)
        with open(HOST_DATA_FILE, "r") as f:
            data = json_lib.load(f)
        return data, age
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def get_db():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "archie_database"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "archie"),
        user=os.getenv("DB_USER", "archie"),
        password=_get_db_password(),
    )


# ---------------------------------------------------------------------------
# Audit & metrics
# ---------------------------------------------------------------------------


def audit_log(
    action_type: str,
    target: str,
    details: dict = None,
    success: bool = True,
    error_message: str = None,
):
    """Log system operation to audit table"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO system_operations_audit
            (user_id, action_type, target, details, success, error_message, ip_address)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """,
            (
                current_user.id if current_user.is_authenticated else None,
                action_type,
                target,
                Json(details) if details else None,
                success,
                error_message,
                request.remote_addr,
            ),
        )
        audit_id = cur.fetchone()[0]
        conn.commit()
        return audit_id
    except Exception as e:
        print(f"Audit log error: {e}")
        return None
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def record_metric(metric_type: str, value: float, details: dict = None):
    """Record a system metric for historical tracking"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO system_metrics_history (metric_type, value, details)
            VALUES (%s, %s, %s)
        """,
            (metric_type, value, Json(details) if details else None),
        )
        conn.commit()

        # Run cleanup (delete records older than 30 days) - every 100th insert
        import random

        if random.randint(1, 100) == 1:
            cur.execute("SELECT cleanup_old_system_metrics()")
            conn.commit()

    except Exception as e:
        print(f"Metric recording error: {e}")
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def require_reauth():
    """Check if user has recently re-authenticated (within 5 minutes).
    Returns None if OK, or a JSON error response if re-auth needed."""
    from flask import session

    reauth_time = session.get("reauth_timestamp")
    if reauth_time and (time_mod.time() - reauth_time) < 300:
        return None
    return (
        jsonify(
            {
                "success": False,
                "error": "Re-authentication required for this operation",
                "requires_reauth": True,
            }
        ),
        401,
    )


# ---------------------------------------------------------------------------
# Docker compose helpers
# ---------------------------------------------------------------------------


def parse_compose_host_port(port_entry):
    """Parse a docker-compose port entry and return the host port as int, or None.
    Handles string formats: "3000:3000", "0.0.0.0:3000:3000", "3000:3000/tcp", "3000"
    and dict format: {"target": 8080, "published": 80, "protocol": "tcp"}
    """
    if isinstance(port_entry, dict):
        published = port_entry.get("published")
        if published is not None:
            try:
                return int(published)
            except (ValueError, TypeError):
                return None
        return None
    port_str = str(port_entry).split("/")[0]  # strip protocol
    parts = port_str.split(":")
    try:
        if len(parts) == 1:
            return int(parts[0])
        elif len(parts) == 2:
            return int(parts[0])
        else:
            return int(parts[1])
    except (ValueError, IndexError):
        return None


def find_compose_file(compose_dir):
    """Find the docker-compose file in a directory. Returns path or None."""
    for fname in [
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    ]:
        candidate = os.path.join(compose_dir, fname)
        if os.path.exists(candidate):
            return candidate
    return None
