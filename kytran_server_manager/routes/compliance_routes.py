"""
KSM — Compliance Routes
========================
Endpoints for compliance scanning, SOC 2 scores, and evidence collection.
"""

import threading

from flask import jsonify, request
from flask_login import login_required

from ..services import compliance_service


def register_compliance_routes(bp, admin_required_decorator):
    """Register compliance-related routes on the given blueprint."""

    @bp.route("/api/compliance/scan", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_compliance_scan():
        """Trigger a compliance scan (runs in background thread)."""
        try:
            data = request.get_json(silent=True) or {}
            pack_ids = data.get("pack_ids")
            triggered_by = data.get("triggered_by", "manual")

            # Load packs first (fast)
            compliance_service.load_all_packs()

            # Run scan synchronously (typically 10-30s)
            result = compliance_service.run_scan(
                pack_ids=pack_ids, triggered_by=triggered_by
            )
            return jsonify({"success": True, **result})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/compliance/scores")
    @login_required
    @admin_required_decorator
    def api_compliance_scores():
        """Get latest scan scores with per-pack breakdown."""
        try:
            scores = compliance_service.get_latest_scores()
            if scores is None:
                return jsonify({"success": True, "data": None,
                                "message": "No completed scans yet"})
            return jsonify({"success": True, "data": scores})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/compliance/scans")
    @login_required
    @admin_required_decorator
    def api_compliance_history():
        """Get scan history."""
        try:
            limit = request.args.get("limit", 20, type=int)
            scans = compliance_service.get_scan_history(limit=limit)
            return jsonify({"success": True, "scans": scans})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/compliance/scans/<scan_id>")
    @login_required
    @admin_required_decorator
    def api_compliance_scan_results(scan_id):
        """Get individual check results for a scan."""
        try:
            status = request.args.get("status")
            severity = request.args.get("severity")
            pack_id = request.args.get("pack_id")
            results = compliance_service.get_scan_results(
                scan_id, status=status, severity=severity, pack_id=pack_id
            )
            return jsonify({"success": True, "results": results})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/compliance/evidence/collect", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_compliance_evidence_collect():
        """Collect SOC 2 evidence artifacts."""
        try:
            data = request.get_json(silent=True) or {}
            scan_id = data.get("scan_id")
            result = compliance_service.collect_evidence(scan_id=scan_id)
            return jsonify({"success": True, **result})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/compliance/evidence")
    @login_required
    @admin_required_decorator
    def api_compliance_evidence_list():
        """List collected evidence."""
        try:
            from ..db import get_db
            conn = get_db()
            try:
                rows = conn.execute(
                    """SELECT id, control_id, artifact_type, artifact_name,
                              collected_at, scan_id
                       FROM compliance_evidence
                       ORDER BY collected_at DESC LIMIT 100"""
                ).fetchall()
                return jsonify({"success": True, "evidence": [dict(r) for r in rows]})
            finally:
                conn.close()
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/compliance/soc2")
    @login_required
    @admin_required_decorator
    def api_compliance_soc2():
        """Get SOC 2 Trust Service Criteria scores."""
        try:
            scan_id = request.args.get("scan_id")
            scores = compliance_service.get_soc2_scores(scan_id=scan_id)
            if scores is None:
                return jsonify({"success": True, "data": None,
                                "message": "No completed scans yet"})
            return jsonify({"success": True, "data": scores})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/compliance/packs")
    @login_required
    @admin_required_decorator
    def api_compliance_packs():
        """List loaded rule packs."""
        try:
            from ..db import get_db
            conn = get_db()
            try:
                rows = conn.execute(
                    "SELECT pack_id, name, updated_at FROM compliance_rule_packs ORDER BY name"
                ).fetchall()
                packs = []
                for r in rows:
                    r = dict(r)
                    packs.append({"pack_id": r["pack_id"], "name": r["name"],
                                  "updated_at": r["updated_at"]})
                return jsonify({"success": True, "packs": packs})
            finally:
                conn.close()
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/compliance/packs/load", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_compliance_packs_load():
        """Reload all rule packs from disk."""
        try:
            results = compliance_service.load_all_packs()
            return jsonify({"success": True, "loaded": results})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
