"""
Compliance API Routes
=====================
REST endpoints for compliance scanning, results, and remediation.
"""

import io
import logging

from flask import jsonify, request, Response, send_file
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

    @bp.route("/api/compliance/latest", methods=["GET"])
    @login_required
    @admin_required_decorator
    def compliance_latest_combined():
        """Get the latest scan summary + its results (used by dashboard JS)."""
        try:
            scores = compliance_service.get_latest_scores()
            if scores is None:
                return jsonify({"success": False, "message": "No completed scans yet"}), 404

            scan_id = scores.get("scan_id")
            results = compliance_service.get_scan_results(scan_id) if scan_id else []

            return jsonify({
                "success": True,
                "scan": {
                    "scan_id": scan_id,
                    "score": scores.get("score", 0),
                    "passed": scores.get("passed", 0),
                    "failed": scores.get("failed", 0),
                    "total_rules": scores.get("total_rules", 0),
                    "completed_at": scores.get("completed_at"),
                    "started_at": scores.get("started_at"),
                },
                "results": results,
            })
        except Exception as exc:
            logger.error("Failed to get latest compliance data: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 500

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

    # ------------------------------------------------------------------
    # Report routes (business tier)
    # ------------------------------------------------------------------

    @bp.route("/compliance/report/<int:scan_id>")
    @login_required
    @require_tier("business")
    def compliance_report_view(scan_id):
        """Render an HTML compliance report for the given scan."""
        from ..services.report_service import render_html_report

        try:
            html = render_html_report(scan_id)
            if html is None:
                return jsonify({"error": "Scan not found"}), 404
            return Response(html, mimetype="text/html")
        except Exception as exc:
            logger.error("Report render failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/report/<int:scan_id>/pdf")
    @login_required
    @require_tier("business")
    def compliance_report_pdf(scan_id):
        """Generate and download a PDF compliance report."""
        from ..services.report_service import generate_pdf_report

        try:
            pdf_bytes, filename = generate_pdf_report(scan_id)
            if pdf_bytes is None:
                return jsonify({"error": "Scan not found or PDF generation unavailable"}), 500
            return send_file(
                io.BytesIO(pdf_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=filename,
            )
        except Exception as exc:
            logger.error("PDF report failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/compliance/reports")
    @login_required
    @require_tier("business")
    def compliance_report_list():
        """List all saved PDF reports."""
        from ..services.report_service import list_reports

        try:
            reports = list_reports()
            return jsonify({"success": True, "reports": reports})
        except Exception as exc:
            logger.error("Report list failed: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 500

    # ------------------------------------------------------------------
    # S.H.I.E.L.D. AI analysis endpoints
    # ------------------------------------------------------------------

    @bp.route("/api/compliance/report/ai", methods=["POST"])
    @login_required
    @admin_required_decorator
    def compliance_trigger_ai_analysis():
        """Trigger S.H.I.E.L.D. AI analysis for the latest or a given scan.

        Body (optional JSON):
            scan_id: specific scan ID to analyse (defaults to latest)

        Returns:
            {success, triggered, message} — 'triggered' is False when hub is not
            configured or the scan is not found.
        """
        from ..services.hub_client import is_hub_configured, trigger_ai_analysis
        from ..services.compliance_service import get_latest_scores, get_scan_results

        if not is_hub_configured():
            return jsonify({
                "success": False,
                "triggered": False,
                "message": "ARCHIE hub not configured — set KSM_ARCHIE_HUB_URL and KSM_ARCHIE_CLIENT_SECRET",
            }), 503

        data = request.get_json(silent=True) or {}
        scan_id = data.get("scan_id")

        # Resolve scan_id if not provided
        if not scan_id:
            try:
                scores = get_latest_scores()
                if scores:
                    scan_id = scores.get("scan_id")
            except Exception as exc:
                logger.warning("Could not fetch latest scan: %s", exc)

        if not scan_id:
            return jsonify({
                "success": False,
                "triggered": False,
                "message": "No scan found — run a compliance scan first",
            }), 404

        # Build scan payload for the hub
        try:
            scores = get_latest_scores()
            results = get_scan_results(scan_id, status="fail")
            failed_rules = [
                {
                    "rule_id": r.get("rule_id", ""),
                    "severity": r.get("severity", ""),
                    "description": r.get("description", ""),
                }
                for r in (results or [])
            ]

            pack_scores = {}
            if scores:
                for ps in scores.get("pack_scores", []):
                    pack_scores[ps.get("pack_id", "unknown")] = ps.get("score", 0)

            scan_payload = {
                "scan_id": str(scan_id),
                "score": scores.get("overall_score", 0) if scores else 0,
                "overall_score": scores.get("overall_score", 0) if scores else 0,
                "total_rules": scores.get("total_rules", 0) if scores else 0,
                "passed": scores.get("passed", 0) if scores else 0,
                "failed": scores.get("failed", 0) if scores else 0,
                "pack_ids": list(pack_scores.keys()),
                "pack_scores": pack_scores,
                "failed_rules": failed_rules,
            }

            ok = trigger_ai_analysis(scan_payload)
            if ok:
                return jsonify({
                    "success": True,
                    "triggered": True,
                    "scan_id": scan_id,
                    "message": "S.H.I.E.L.D. analysis triggered — results available in 30-120 seconds",
                })
            else:
                return jsonify({
                    "success": False,
                    "triggered": False,
                    "message": "Hub rejected the request — check hub connectivity and credentials",
                }), 502

        except Exception as exc:
            logger.error("AI analysis trigger failed: %s", exc)
            return jsonify({"success": False, "triggered": False, "error": str(exc)}), 500

    @bp.route("/api/compliance/report/ai", methods=["GET"])
    @login_required
    @admin_required_decorator
    def compliance_fetch_ai_analysis():
        """Fetch the latest S.H.I.E.L.D. AI analysis from the ARCHIE hub.

        Returns:
            {success, analysis} where analysis has executive_summary,
            remediation_plan, trend_analysis, model_used, generated_at.
            analysis is null when no analysis exists yet.
        """
        from ..services.hub_client import is_hub_configured, fetch_ai_analysis

        if not is_hub_configured():
            return jsonify({
                "success": False,
                "analysis": None,
                "message": "ARCHIE hub not configured",
            }), 503

        try:
            analysis = fetch_ai_analysis(scan_target="Kytran System Operations")
            return jsonify({"success": True, "analysis": analysis})
        except Exception as exc:
            logger.error("AI analysis fetch failed: %s", exc)
            return jsonify({"success": False, "analysis": None, "error": str(exc)}), 500
