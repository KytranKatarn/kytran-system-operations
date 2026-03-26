"""
Kytran System Operations — Shared helpers and constants
=====================================================
Standalone version adapted from System Operations helpers.
Uses SQLite instead of PostgreSQL.
"""

from flask import jsonify, request
from flask_login import current_user
import os
import json as json_lib
import time as time_mod

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_DIR = os.environ.get("KSM_BASE_DIR", "/")
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
# Database (SQLite)
# ---------------------------------------------------------------------------


def get_db():
    """Get database connection (SQLite)."""
    from .db import get_db as _get_db
    return _get_db()


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
    """Log system operation to audit table (SQLite version)."""
    try:
        conn = get_db()
        cur = conn.cursor()
        username = getattr(current_user, "username", None) if current_user and hasattr(current_user, "is_authenticated") and current_user.is_authenticated else None
        ip_addr = request.remote_addr if request else None
        cur.execute(
            """INSERT INTO audit_log
            (action_type, target, details, success, error_message, username, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                action_type,
                target,
                json_lib.dumps(details) if details else None,
                1 if success else 0,
                error_message,
                username,
                ip_addr,
            ),
        )
        audit_id = cur.lastrowid
        conn.commit()
        conn.close()
        return audit_id
    except Exception as e:
        print(f"Audit log error: {e}")
        return None


def record_metric(metric_type: str, value: float, details: dict = None):
    """Record a system metric for historical tracking (SQLite version)."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO system_metrics_history (metric_type, value, details)
            VALUES (?, ?, ?)""",
            (metric_type, value, json_lib.dumps(details) if details else None),
        )
        conn.commit()

        # Run cleanup (delete records older than 30 days) - every 100th insert
        import random
        if random.randint(1, 100) == 1:
            cur.execute(
                "DELETE FROM system_metrics_history WHERE recorded_at < datetime('now', '-30 days')"
            )
            conn.commit()

        conn.close()
    except Exception as e:
        print(f"Metric recording error: {e}")


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
    """Parse a docker-compose port entry and return the host port as int, or None."""
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
