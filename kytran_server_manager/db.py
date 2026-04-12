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
        color TEXT DEFAULT '#2563eb',
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
    conn.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS compliance_rule_packs (
        pack_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        version TEXT DEFAULT '1.0',
        source TEXT DEFAULT 'DISA',
        total_rules INTEGER DEFAULT 0,
        rules TEXT DEFAULT '[]',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS compliance_scans (
        scan_id TEXT PRIMARY KEY,
        triggered_by TEXT DEFAULT 'manual',
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        pack_ids TEXT DEFAULT '[]',
        total_rules INTEGER DEFAULT 0,
        passed INTEGER DEFAULT 0,
        failed INTEGER DEFAULT 0,
        errors INTEGER DEFAULT 0,
        score REAL DEFAULT 0.0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS compliance_scan_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id TEXT NOT NULL,
        pack_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        severity TEXT,
        status TEXT,
        actual_value TEXT,
        expected_value TEXT,
        details TEXT,
        soc2_controls TEXT DEFAULT '[]'
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS compliance_fixes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        user_id INTEGER,
        fix_type TEXT,
        command_executed TEXT,
        success INTEGER DEFAULT 0,
        error_message TEXT,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stack_health_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stack_name TEXT,
        container_name TEXT,
        alert_type TEXT NOT NULL,
        severity TEXT DEFAULT 'warning',
        message TEXT NOT NULL,
        details TEXT,
        acknowledged INTEGER DEFAULT 0,
        acknowledged_by TEXT,
        acknowledged_at TIMESTAMP,
        resolved INTEGER DEFAULT 0,
        resolved_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stack_health_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stack_name TEXT,
        metric TEXT NOT NULL,
        threshold REAL,
        severity TEXT DEFAULT 'warning',
        enabled INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS health_alert_webhooks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        events TEXT DEFAULT '[]',
        enabled INTEGER DEFAULT 1,
        secret TEXT,
        last_status TEXT,
        last_error TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
        tier TEXT NOT NULL DEFAULT 'free' CHECK(tier IN ('free', 'pro', 'business', 'enterprise')),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'cancelled', 'past_due', 'trialing')),
        stripe_customer_id TEXT,
        stripe_subscription_id TEXT,
        current_period_end TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS compliance_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id TEXT,
        rule_id TEXT,
        pack_id TEXT,
        evidence_type TEXT NOT NULL CHECK(evidence_type IN (
            'command_output', 'file_content', 'config_snapshot',
            'service_status', 'log_excerpt',
            'firewall_config', 'access_control_config', 'audit_log_export',
            'ssl_cert_status', 'service_inventory'
        )),
        content TEXT NOT NULL,
        soc2_mapping TEXT,
        collected_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_scan ON compliance_evidence(scan_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_soc2 ON compliance_evidence(soc2_mapping)")
    # Migrations — add columns that may be missing from older DBs
    for col in ["display_name TEXT", "email TEXT", "sso_provider TEXT DEFAULT NULL"]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col}")
        except Exception:
            pass  # Column already exists
    try:
        conn.execute("ALTER TABLE docker_stacks ADD COLUMN color TEXT DEFAULT '#2563eb'")
    except Exception:
        pass  # Column already exists
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn
