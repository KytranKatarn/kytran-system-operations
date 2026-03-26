"""
Compliance API Routes
=====================
REST endpoints for compliance scanning, results, and remediation.
"""

import logging

from flask import jsonify, request, Response
from flask_login import login_required, current_user

from ..services import compliance_service
from ..middleware.tier_gate import require_tier

logger = logging.getLogger(__name__)


def register_compliance_routes(bp, admin_required_decorator):
    """Register all compliance endpoints on the security_network blueprint."""

    @bp.route("/api/compliance/scan", methods=["POST"])
    @login_required
    @admin_required_decorator
    def compliance_run_scan():
        """Trigger a compliance scan."""
        data = request.get_json(silent=True) or {}
        pack_ids = data.get("pack_ids")

        # --- Tier-gate: filter packs by subscription tier ---
        from ..services.subscription_service import get_user_tier, get_allowed_packs

        tier = get_user_tier(current_user.id)
        allowed = get_allowed_packs(tier)
        if pack_ids:
            blocked = [p for p in pack_ids if p not in allowed]
            if blocked:
                return jsonify({
                    "error": "upgrade_required",
                    "message": f"Pack(s) {blocked} require a higher tier.",
                    "allowed_packs": allowed,
                    "current_tier": tier,
                }), 403
            pack_ids = [p for p in pack_ids if p in allowed]
        else:
            pack_ids = allowed

        try:
            result = compliance_service.run_scan(pack_ids=pack_ids, triggered_by="manual")
            return jsonify(result)
        except Exception as exc:
            logger.error("Compliance scan failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/scans", methods=["GET"])
    @login_required
    @admin_required_decorator
    def compliance_scan_history():
        """Get scan history."""
        limit = request.args.get("limit", 20, type=int)
        try:
            scans = compliance_service.get_scan_history(limit=limit)
            return jsonify({"scans": scans})
        except Exception as exc:
            logger.error("Failed to get scan history: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/scans/<scan_id>", methods=["GET"])
    @login_required
    @admin_required_decorator
    def compliance_scan_results(scan_id):
        """Get results for a specific scan."""
        status = request.args.get("status")
        severity = request.args.get("severity")
        pack_id = request.args.get("pack_id")
        try:
            results = compliance_service.get_scan_results(scan_id, status=status, severity=severity, pack_id=pack_id)
            return jsonify({"scan_id": scan_id, "results": results, "count": len(results)})
        except Exception as exc:
            logger.error("Failed to get scan results: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/scores", methods=["GET"])
    @login_required
    @admin_required_decorator
    def compliance_latest_scores():
        """Get latest compliance scores."""
        try:
            scores = compliance_service.get_latest_scores()
            if scores is None:
                return jsonify({"success": False, "message": "No completed scans yet"}), 404
            scores["success"] = True
            return jsonify(scores)
        except Exception as exc:
            logger.error("Failed to get scores: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/packs", methods=["GET"])
    @login_required
    @admin_required_decorator
    def compliance_list_packs():
        """List loaded rule packs."""
        try:
            packs = compliance_service.get_loaded_packs()
            return jsonify({"packs": packs})
        except Exception as exc:
            logger.error("Failed to list packs: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/fix", methods=["POST"])
    @login_required
    @admin_required_decorator
    @require_tier("business")
    def compliance_apply_fix():
        """Apply a remediation fix."""
        data = request.get_json(silent=True) or {}
        scan_id = data.get("scan_id")
        rule_id = data.get("rule_id")
        if not scan_id or not rule_id:
            return jsonify({"error": "scan_id and rule_id are required"}), 400
        try:
            result = compliance_service.apply_fix(scan_id, rule_id, user_id=current_user.id)
            status_code = 200 if result.get("success") else 400
            return jsonify(result), status_code
        except Exception as exc:
            logger.error("Fix failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/export/ckl", methods=["GET"])
    @login_required
    @admin_required_decorator
    def compliance_export_ckl():
        """Export scan results as STIG Viewer .ckl XML file."""
        scan_id = request.args.get("scan_id")
        if not scan_id:
            return jsonify({"error": "scan_id parameter required"}), 400
        try:
            xml_str = compliance_service.generate_ckl(scan_id)
            if xml_str is None:
                return jsonify({"error": "Scan not found"}), 404
            return Response(
                xml_str,
                mimetype="application/xml",
                headers={"Content-Disposition": f'attachment; filename="compliance-scan-{scan_id}.ckl"'},
            )
        except Exception as exc:
            logger.error("CKL export failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/export/evidence", methods=["GET"])
    @login_required
    @admin_required_decorator
    def compliance_export_evidence():
        """Export scan evidence as ZIP file (summary JSON + CSV + CKL)."""
        scan_id = request.args.get("scan_id")
        if not scan_id:
            return jsonify({"error": "scan_id parameter required"}), 400
        try:
            zip_bytes = compliance_service.generate_evidence_zip(scan_id)
            if zip_bytes is None:
                return jsonify({"error": "Scan not found"}), 404
            return Response(
                zip_bytes,
                mimetype="application/zip",
                headers={"Content-Disposition": f'attachment; filename="compliance-evidence-{scan_id}.zip"'},
            )
        except Exception as exc:
            logger.error("Evidence export failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/evidence/collect", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_collect_evidence():
        """Trigger evidence collection for SOC 2."""
        scan_id = request.json.get("scan_id") if request.is_json else None
        try:
            result = compliance_service.collect_evidence(scan_id=scan_id)
            return jsonify({"success": True, **result})
        except Exception as exc:
            logger.error("Evidence collection failed: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 500

    @bp.route("/api/compliance/evidence", methods=["GET"])
    @login_required
    @admin_required_decorator
    def api_list_evidence():
        """List collected evidence artifacts."""
        from ..db import get_db

        try:
            db = get_db()
            rows = db.execute(
                """
                SELECT id, control_id, artifact_type, artifact_name, collected_at
                FROM compliance_evidence
                ORDER BY collected_at DESC LIMIT 50
                """
            ).fetchall()
            db.close()
            evidence = [dict(r) for r in rows]
            for e in evidence:
                if e.get("collected_at") and hasattr(e["collected_at"], "isoformat"):
                    e["collected_at"] = e["collected_at"].isoformat()
            return jsonify({"success": True, "evidence": evidence})
        except Exception as exc:
            logger.error("Evidence list failed: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 500

    @bp.route("/api/compliance/report/<scan_id>")
    @login_required
    @admin_required_decorator
    def compliance_pdf_report(scan_id):
        """Generate and serve a branded compliance report (print-to-PDF)."""
        client_name = request.args.get("client", "System Assessment")
        try:
            html = compliance_service.generate_report_html(scan_id, client_name=client_name)
            if html is None:
                return jsonify({"error": "Scan not found"}), 404
            return Response(html, mimetype="text/html")
        except Exception as exc:
            logger.error("Report generation failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/packs/reload", methods=["POST"])
    @login_required
    @admin_required_decorator
    def compliance_reload_packs():
        """Reload rule packs from disk."""
        try:
            results = compliance_service.load_all_packs()
            return jsonify({"loaded": results})
        except Exception as exc:
            logger.error("Pack reload failed: %s", exc)
            return jsonify({"error": str(exc)}), 500
