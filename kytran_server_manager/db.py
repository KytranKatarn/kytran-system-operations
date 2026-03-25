"""SQLite database for standalone deployment."""
import sqlite3
import os

_db_path = None


def init_db(path):
    global _db_path
    _db_path = path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'admin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_hash TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        scope TEXT DEFAULT 'read-only',
        user_id INTEGER REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action_type TEXT NOT NULL,
        target TEXT,
        details TEXT,
        success INTEGER DEFAULT 1,
        error_message TEXT,
        username TEXT,
        ip_address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS system_metrics_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        metric_type TEXT NOT NULL,
        value REAL NOT NULL,
        details TEXT,
        recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS docker_stacks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        display_name TEXT,
        compose_directory TEXT NOT NULL,
        description TEXT,
        is_system INTEGER DEFAULT 0,
        auto_start INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS health_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_type TEXT NOT NULL,
        severity TEXT DEFAULT 'warning',
        message TEXT NOT NULL,
        details TEXT,
        acknowledged INTEGER DEFAULT 0,
        acknowledged_by TEXT,
        acknowledged_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS webhooks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        events TEXT DEFAULT '[]',
        enabled INTEGER DEFAULT 1,
        secret TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS compliance_rule_packs (
        pack_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        rules TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS compliance_scans (
        scan_id TEXT PRIMARY KEY,
        triggered_by TEXT DEFAULT 'manual',
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        pack_ids TEXT,
        total_rules INTEGER,
        passed INTEGER,
        failed INTEGER,
        errors INTEGER,
        score REAL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS compliance_scan_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id TEXT NOT NULL,
        pack_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        severity TEXT,
        status TEXT NOT NULL,
        actual_value TEXT,
        expected_value TEXT,
        details TEXT,
        soc2_controls TEXT,
        scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS compliance_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        control_id TEXT NOT NULL,
        artifact_type TEXT NOT NULL,
        artifact_name TEXT,
        artifact_data TEXT,
        collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        scan_id TEXT
    )""")
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn
