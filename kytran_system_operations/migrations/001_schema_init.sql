-- =============================================================================
-- Kytran System Operations — Standalone Schema Init
-- =============================================================================
-- Creates all tables the copied platform system_operations modules expect.
-- Schemas mirror ARCHIE migrations 071 + 075 plus stubs for cross-module
-- tables (compliance, fleet) that the standalone dashboard reads but does
-- not own. The stub tables are empty — queries return no rows, dashboard
-- tiles show "no data yet".
-- =============================================================================

-- -----------------------------------------------------------------------------
-- NOTE on users: the standalone's auth layer uses SQLite (kytran_system_operations/db.py).
-- Audit tables below store user_id as a bare INTEGER — no cross-DB FK. Values
-- reference users.id from the SQLite database.
-- -----------------------------------------------------------------------------

-- -----------------------------------------------------------------------------
-- Migration 071: System metrics history + audit log
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_metrics_history (
    id SERIAL PRIMARY KEY,
    metric_type VARCHAR(50) NOT NULL,
    value DECIMAL(10, 2) NOT NULL,
    details JSONB,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_metrics_history_type_time ON system_metrics_history(metric_type, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_history_recorded ON system_metrics_history(recorded_at);

CREATE TABLE IF NOT EXISTS system_operations_audit (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,  -- references SQLite users.id, no FK across DBs
    action_type VARCHAR(100) NOT NULL,
    target VARCHAR(255) NOT NULL,
    details JSONB,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    ip_address VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sysops_audit_user ON system_operations_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_sysops_audit_type ON system_operations_audit(action_type);
CREATE INDEX IF NOT EXISTS idx_sysops_audit_created ON system_operations_audit(created_at DESC);

CREATE TABLE IF NOT EXISTS system_alerts_config (
    id SERIAL PRIMARY KEY,
    metric_type VARCHAR(50) NOT NULL UNIQUE,
    threshold_warning DECIMAL(10,2),
    threshold_critical DECIMAL(10,2),
    enabled BOOLEAN DEFAULT TRUE,
    notify_email BOOLEAN DEFAULT FALSE,
    notify_webhook BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO system_alerts_config (metric_type, threshold_warning, threshold_critical)
VALUES
    ('cpu', 75.0, 90.0),
    ('memory', 80.0, 95.0),
    ('disk', 80.0, 90.0),
    ('gpu', 85.0, 95.0)
ON CONFLICT (metric_type) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Migration 075: Stack health alerts + webhooks + thresholds
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stack_health_alerts (
    id SERIAL PRIMARY KEY,
    stack_name VARCHAR(50) NOT NULL,
    container_name VARCHAR(100),
    alert_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) DEFAULT 'warning',
    message TEXT NOT NULL,
    details JSONB,
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by INTEGER,  -- references SQLite users.id
    acknowledged_at TIMESTAMP,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stack_health_config (
    id SERIAL PRIMARY KEY,
    stack_name VARCHAR(50),
    metric_type VARCHAR(50) NOT NULL,
    threshold_warning DECIMAL(10,2),
    threshold_critical DECIMAL(10,2),
    enabled BOOLEAN DEFAULT TRUE,
    cooldown_minutes INTEGER DEFAULT 15,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stack_name, metric_type)
);

CREATE TABLE IF NOT EXISTS health_alert_webhooks (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    url TEXT NOT NULL,
    event_types TEXT[] DEFAULT ARRAY['container_crash', 'health_warning', 'health_critical'],
    stack_filter TEXT[],
    active BOOLEAN DEFAULT TRUE,
    secret_key TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_triggered TIMESTAMP,
    last_status INTEGER,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS container_metrics_history (
    id SERIAL PRIMARY KEY,
    container_name VARCHAR(100) NOT NULL,
    stack_name VARCHAR(50),
    cpu_percent DECIMAL(5,2),
    mem_percent DECIMAL(5,2),
    mem_usage_mb INTEGER,
    restart_count INTEGER DEFAULT 0,
    health_status VARCHAR(20),
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_health_alerts_stack ON stack_health_alerts(stack_name);
CREATE INDEX IF NOT EXISTS idx_health_alerts_created ON stack_health_alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_container ON container_metrics_history(container_name, recorded_at DESC);

INSERT INTO stack_health_config (stack_name, metric_type, threshold_warning, threshold_critical, cooldown_minutes)
VALUES
    (NULL, 'cpu', 80.0, 95.0, 15),
    (NULL, 'memory', 85.0, 95.0, 15),
    (NULL, 'restart_count', 3, 5, 30)
ON CONFLICT (stack_name, metric_type) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Standalone-owned: docker_stacks, storage_mounts
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS docker_stacks (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    display_name VARCHAR(200),
    description TEXT,
    color VARCHAR(20),
    compose_directory TEXT,
    is_system BOOLEAN DEFAULT FALSE,
    auto_start BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS storage_mounts (
    id SERIAL PRIMARY KEY,
    device VARCHAR(255) NOT NULL,
    mount_point VARCHAR(255) NOT NULL,
    filesystem VARCHAR(50),
    label VARCHAR(100),
    capacity_gb DECIMAL(12, 2),
    drive_model VARCHAR(200),
    is_managed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- Cross-module stubs (empty — dashboard tiles show "no data yet")
-- These tables are owned by Security Network in ARCHIE. In the standalone
-- they stay empty; the compliance module has its own scanner and writes to
-- its own tables (via compliance_routes.py).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS compliance_scans (
    scan_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    triggered_by VARCHAR(50),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    score DECIMAL(5,2),
    passed INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS compliance_scan_results (
    id SERIAL PRIMARY KEY,
    scan_id UUID NOT NULL,
    pack_id VARCHAR(100) NOT NULL,
    rule_id VARCHAR(50) NOT NULL,
    severity VARCHAR(20),
    status VARCHAR(20) NOT NULL,
    actual_value TEXT,
    expected_value TEXT,
    details TEXT,
    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS remote_nodes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    hostname VARCHAR(255),
    deployment_mode VARCHAR(50) DEFAULT 'standalone',
    status VARCHAR(50) DEFAULT 'offline',
    last_heartbeat TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
