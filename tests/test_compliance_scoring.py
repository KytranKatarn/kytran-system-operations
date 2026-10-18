import json
import sqlite3

import pytest
from flask import Flask

from kytran_system_operations import db
from kytran_system_operations.services.compliance_service import (
    _compliance_score,
    _normalize_scan_score,
    run_scan,
)


def test_non_applicable_controls_are_excluded_from_score():
    assert _compliance_score(128, 178, 20) == 81.0


def test_fully_assessed_scan_uses_all_rules():
    assert _compliance_score(157, 178, 0) == 88.2


def test_no_applicable_controls_has_no_score():
    assert _compliance_score(0, 12, 12) is None


def test_not_applicable_cannot_exceed_total():
    with pytest.raises(ValueError):
        _compliance_score(0, 10, 11)


def test_legacy_scan_score_is_normalized_without_counting_not_applicable_as_errors():
    score, errors, basis = _normalize_scan_score(
        71.9, 128, 178, 20, 20, "legacy_all_rules_v1"
    )
    assert (score, errors, basis) == (81.0, 0, "applicable_controls_v2")


def test_current_scan_preserves_genuine_errors():
    score, errors, basis = _normalize_scan_score(
        81.0, 128, 178, 20, 3, "applicable_controls_v2"
    )
    assert (score, errors, basis) == (81.0, 3, "applicable_controls_v2")


def test_run_scan_reports_not_applicable_separately(tmp_path, monkeypatch):
    db_path = tmp_path / "data.db"
    old_path = db._db_path
    db.init_db(str(db_path))
    try:
        rules = [
            {"rule_id": "na-1", "container_not_applicable": True, "check": {}},
            {"rule_id": "na-2", "container_not_applicable": True, "check": {}},
            {"rule_id": "pass-1", "check": {"expected": "pass"}},
            {"rule_id": "fail-1", "check": {"expected": "fail"}},
        ]
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO compliance_rule_packs (pack_id, name, rules) VALUES (?, ?, ?)",
                ("fixture", "Fixture", json.dumps(rules)),
            )

        monkeypatch.setattr(
            "kytran_system_operations.services.compliance_service._get_host_client",
            lambda: {"available": lambda: False, "run": None},
        )
        monkeypatch.setattr(
            "kytran_system_operations.services.compliance_service._is_running_in_container",
            lambda: True,
        )
        monkeypatch.setattr(
            "kytran_system_operations.services.compliance_service._execute_check",
            lambda check: {
                "status": "pass" if check["expected"] == "pass" else "fail",
                "actual": "fixture",
                "expected": check["expected"],
                "details": "",
            },
        )

        app = Flask(__name__)
        app.config.update(ARCHIE_HUB_URL="", ARCHIE_CLIENT_SECRET="")
        with app.app_context():
            result = run_scan(pack_ids=["fixture"])

        assert result["total"] == 4
        assert result["passed"] == 1
        assert result["failed"] == 1
        assert result["errors"] == 0
        assert result["not_applicable"] == 2
        assert result["assessed"] == 2
        assert result["coverage"] == 50.0
        assert result["score"] == 50.0
        assert result["pack_scores"]["fixture"]["not_applicable"] == 2
    finally:
        db._db_path = old_path


def test_legacy_database_adds_scoring_provenance_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    old_path = db._db_path
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE compliance_scans (
                scan_id TEXT PRIMARY KEY, node_id TEXT, triggered_by TEXT,
                started_at TIMESTAMP, completed_at TIMESTAMP, pack_ids TEXT,
                total_rules INTEGER, passed INTEGER, failed INTEGER, errors INTEGER,
                score REAL
            )"""
        )

    try:
        db.init_db(str(db_path))
        with sqlite3.connect(db_path) as conn:
            columns = {row[1]: row[4] for row in conn.execute("PRAGMA table_info(compliance_scans)")}
        assert columns["not_applicable"] == "0"
        assert columns["score_basis"] == "'legacy_all_rules_v1'"
    finally:
        db._db_path = old_path
