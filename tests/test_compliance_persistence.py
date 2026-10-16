"""Real temporary SQLite storage; no host checks, network, or production DB."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kytran_system_operations.services import compliance_service as service


class CompliancePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "scan.db")

        def connect():
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            return db

        self.connect = connect
        with connect() as db:
            db.executescript("""
                CREATE TABLE compliance_rule_packs(pack_id TEXT, name TEXT, rules TEXT);
                CREATE TABLE compliance_scans(scan_id TEXT, triggered_by TEXT, started_at TEXT,
                    pack_ids TEXT, completed_at TEXT, total_rules INT, passed INT, failed INT,
                    errors INT, score REAL);
                CREATE TABLE compliance_scan_results(scan_id TEXT, pack_id TEXT, rule_id TEXT,
                    severity TEXT, status TEXT, actual_value TEXT, expected_value TEXT,
                    details TEXT, soc2_controls TEXT);
                CREATE TABLE compliance_evidence(scan_id TEXT, rule_id TEXT, pack_id TEXT,
                    evidence_type TEXT, content TEXT, soc2_mapping TEXT);
            """)
        for name, value in [
            ("get_db", connect),
            ("sync_rule_packs_from_hub", lambda: None),
            ("_clear_file_cache", lambda: None),
            ("_get_host_client", lambda: {"available": lambda: False}),
            ("_execute_check", lambda _: {"status": "pass"}),
            ("_is_running_in_container", lambda: True),
        ]:
            p = patch.object(service, name, value)
            p.start()
            self.addCleanup(p.stop)

    def seed(self, rules=None):
        if rules is None:
            rules = [{"rule_id": "safe-test", "check": {}}]
        with self.connect() as db:
            db.execute("INSERT INTO compliance_rule_packs VALUES (?, ?, ?)",
                       ("test-pack", "Test Pack", json.dumps(rules)))

    def fail_inserts(self, table):
        self.assertIn(table, ("compliance_scan_results", "compliance_evidence"))
        with self.connect() as db:
            db.execute(f"CREATE TRIGGER fail_write BEFORE INSERT ON {table} "
                       "BEGIN SELECT RAISE(ABORT, 'simulated persistence failure'); END")

    def rows(self, sql):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql)]

    def test_success_really_persists_scan_results_and_evidence(self):
        self.seed()
        result = service.run_scan()
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(self.rows("SELECT completed_at FROM compliance_scans")[0]["completed_at"])
        self.assertEqual(len(self.rows("SELECT * FROM compliance_scan_results")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM compliance_evidence")), 1)

    def test_result_failure_keeps_scan_incomplete(self):
        self.seed()
        self.fail_inserts("compliance_scan_results")
        result = service.run_scan()
        self.assertEqual(result["status"], "persistence_error")
        self.assertEqual(result["result_persistence_errors"], 1)
        self.assertIsNone(self.rows("SELECT completed_at FROM compliance_scans")[0]["completed_at"])

    def test_not_applicable_result_failure_also_keeps_scan_incomplete(self):
        self.seed([{"rule_id": "container-skip", "container_not_applicable": True}])
        self.fail_inserts("compliance_scan_results")
        result = service.run_scan()
        self.assertEqual(result["status"], "persistence_error")
        self.assertEqual(result["result_persistence_errors"], 1)

    def test_evidence_failure_is_not_a_complete_audit(self):
        self.seed()
        self.fail_inserts("compliance_evidence")
        result = service.run_scan()
        self.assertEqual(result["status"], "persistence_error")
        self.assertEqual(result["evidence_persistence_errors"], 1)
        self.assertIsNone(self.rows("SELECT completed_at FROM compliance_scans")[0]["completed_at"])

    def test_no_packs_has_no_fictional_scan_id(self):
        result = service.run_scan()
        self.assertEqual(result["status"], "error")
        self.assertNotIn("scan_id", result)
        self.assertEqual(self.rows("SELECT * FROM compliance_scans"), [])

    def test_empty_rules_have_no_fictional_scan_id(self):
        self.seed([])
        result = service.run_scan()
        self.assertEqual(result["status"], "error")
        self.assertNotIn("scan_id", result)
        self.assertEqual(self.rows("SELECT * FROM compliance_scans"), [])


if __name__ == "__main__":
    unittest.main()
