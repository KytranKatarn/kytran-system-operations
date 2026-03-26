"""
System Operations — Health Routes
====================================
Extracted from routes.py during ADR-045 route split refactor.

Endpoints: health alerts CRUD, health config, webhooks, system-health-full,
           public /api/health monitoring endpoint with tier progress.
"""

import time

from flask import jsonify, request, current_app
from flask_login import login_required, current_user

from datetime import datetime

from ..helpers import get_db

# Track process start time for uptime calculation
_start_time = time.time()

try:
    from tools.blueprint.blueprint_intelligence import get_system_health

    BLUEPRINT_HEALTH_AVAILABLE = True
except ImportError:
    BLUEPRINT_HEALTH_AVAILABLE = False


def register_health_routes(bp, admin_required_decorator):
    """Register health-related routes on the given blueprint."""

    # ── Public health check (no auth) ─────────────────────────
    @bp.route("/api/health")
    def api_health():
        """Public monitoring endpoint — returns status, compliance, tier progress."""
        config = current_app.config
        hub_url = config.get("ARCHIE_HUB_URL", "")
        client_secret = config.get("ARCHIE_CLIENT_SECRET", "")
        subdomain = config.get("SERVER_SUBDOMAIN", "")
        version = config.get("VERSION", "1.0.0")

        # Database check + last compliance scan
        db_ok = False
        last_scan = None
        compliance_score = None
        try:
            conn = get_db()
            cur = conn.cursor()
            db_ok = True

            # Get most recent compliance scan
            try:
                cur.execute(
                    """SELECT completed_at, score
                       FROM compliance_scans
                       WHERE completed_at IS NOT NULL
                       ORDER BY completed_at DESC LIMIT 1"""
                )
                row = cur.fetchone()
                if row:
                    last_scan = row["completed_at"]
                    compliance_score = row["score"]
            except Exception:
                # Table may not exist yet
                pass

            cur.close()
            conn.close()
        except Exception:
            pass

        # Check if last scan is within 24 hours
        scan_recent = False
        if last_scan:
            try:
                scan_dt = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
                age_seconds = (datetime.utcnow() - scan_dt.replace(tzinfo=None)).total_seconds()
                scan_recent = age_seconds < 86400  # 24 hours
            except Exception:
                pass

        hub_configured = bool(hub_url)

        # Tier 2 progress auto-detection
        tier_progress = {
            "sso": bool(hub_url and client_secret),
            "compliance_scanning": scan_recent,
            "badges": True,
            "subdomain": bool(subdomain),
            "redirect": hub_configured,
            "health_endpoint": True,
            "responsive": True,
            "code_health_badge": hub_configured,
        }
        completed = sum(1 for v in tier_progress.values() if v)
        total = len(tier_progress)

        return jsonify({
            "status": "operational",
            "version": version,
            "uptime_seconds": round(time.time() - _start_time),
            "database": "connected" if db_ok else "error",
            "last_compliance_scan": last_scan,
            "compliance_score": compliance_score,
            "connected_to_hub": hub_configured,
            "tier_progress": {
                "tier": 2,
                "completed": completed,
                "total": total,
                "percentage": round((completed / total) * 100),
                "items": tier_progress,
            },
        })

    # ── Authenticated health routes ───────────────────────────
    @bp.route("/api/health/alerts")
    @login_required
    @admin_required_decorator
    def get_health_alerts():
        """Get active health alerts"""
        try:
            conn = get_db()
            cur = conn.cursor()

            include_resolved = request.args.get("include_resolved", "false").lower() == "true"
            stack_name = request.args.get("stack")
            limit = request.args.get("limit", 50, type=int)

            query = """
                SELECT id, stack_name, container_name, alert_type, severity, message,
                       details, acknowledged, acknowledged_by, acknowledged_at,
                       resolved, resolved_at, created_at
                FROM stack_health_alerts
                WHERE 1=1
            """
            params = []

            if not include_resolved:
                query += " AND resolved = FALSE"

            if stack_name:
                query += " AND stack_name = ?"
                params.append(stack_name)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            alerts = cur.fetchall()

            # Get counts by severity
            cur.execute(
                """
                SELECT severity, COUNT(*) as count
                FROM stack_health_alerts
                WHERE resolved = FALSE
                GROUP BY severity
            """
            )
            counts = {row["severity"]: row["count"] for row in cur.fetchall()}

            return jsonify(
                {
                    "success": True,
                    "alerts": [dict(a) for a in alerts],
                    "counts": {
                        "critical": counts.get("critical", 0),
                        "warning": counts.get("warning", 0),
                        "info": counts.get("info", 0),
                        "total": sum(counts.values()),
                    },
                }
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/health/alerts/<int:alert_id>/acknowledge", methods=["POST"])
    @login_required
    @admin_required_decorator
    def acknowledge_health_alert(alert_id):
        """Acknowledge a health alert"""
        try:
            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                """
                UPDATE stack_health_alerts
                SET acknowledged = TRUE,
                    acknowledged_by = ?,
                    acknowledged_at = CURRENT_TIMESTAMP
                WHERE id = ? AND acknowledged = FALSE
            """,
                (current_user.id, alert_id),
            )

            result = cur.fetchone()
            conn.commit()

            if result:
                return jsonify({"success": True, "message": "Alert acknowledged"})
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Alert not found or already acknowledged",
                        }
                    ),
                    404,
                )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/health/alerts/<int:alert_id>/resolve", methods=["POST"])
    @login_required
    @admin_required_decorator
    def resolve_health_alert(alert_id):
        """Resolve a health alert"""
        try:
            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                """
                UPDATE stack_health_alerts
                SET resolved = TRUE,
                    resolved_at = CURRENT_TIMESTAMP
                WHERE id = ? AND resolved = FALSE
            """,
                (alert_id,),
            )

            result = cur.fetchone()
            conn.commit()

            if result:
                return jsonify({"success": True, "message": "Alert resolved"})
            else:
                return (
                    jsonify({"success": False, "error": "Alert not found or already resolved"}),
                    404,
                )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/health/config")
    @login_required
    @admin_required_decorator
    def get_health_config():
        """Get health alert configuration"""
        try:
            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                """
                SELECT id, stack_name, metric_type, threshold_warning, threshold_critical,
                       enabled, cooldown_minutes, updated_at
                FROM stack_health_config
                ORDER BY stack_name NULLS FIRST, metric_type
            """
            )
            configs = cur.fetchall()

            return jsonify({"success": True, "configs": [dict(c) for c in configs]})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/health/config", methods=["POST"])
    @login_required
    @admin_required_decorator
    def update_health_config():
        """Update health alert configuration"""
        try:
            data = request.get_json()
            stack_name = data.get("stack_name")  # None for global
            metric_type = data.get("metric_type")
            threshold_warning = data.get("threshold_warning")
            threshold_critical = data.get("threshold_critical")
            enabled = data.get("enabled", True)
            cooldown_minutes = data.get("cooldown_minutes", 15)

            if not metric_type:
                return jsonify({"success": False, "error": "metric_type required"}), 400

            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                """
                INSERT INTO stack_health_config
                (stack_name, metric_type, threshold_warning, threshold_critical, enabled, cooldown_minutes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (stack_name, metric_type)
                DO UPDATE SET
                    threshold_warning = EXCLUDED.threshold_warning,
                    threshold_critical = EXCLUDED.threshold_critical,
                    enabled = EXCLUDED.enabled,
                    cooldown_minutes = EXCLUDED.cooldown_minutes
            """,
                (
                    stack_name,
                    metric_type,
                    threshold_warning,
                    threshold_critical,
                    enabled,
                    cooldown_minutes,
                ),
            )

            result = cur.fetchone()
            conn.commit()

            return jsonify({"success": True, "id": result[0]})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/health/webhooks")
    @login_required
    @admin_required_decorator
    def get_health_webhooks():
        """Get health alert webhooks"""
        try:
            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                """
                SELECT id, name, url, event_types, stack_filter, active,
                       created_at, last_triggered, last_status, last_error
                FROM health_alert_webhooks
                ORDER BY created_at DESC
            """
            )
            webhooks = cur.fetchall()

            return jsonify({"success": True, "webhooks": [dict(w) for w in webhooks]})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/health/webhooks", methods=["POST"])
    @login_required
    @admin_required_decorator
    def create_health_webhook():
        """Create a health alert webhook"""
        try:
            data = request.get_json()
            name = data.get("name")
            url = data.get("url")
            event_types = data.get("event_types", ["container_crash", "health_warning", "health_critical"])
            stack_filter = data.get("stack_filter")  # None for all stacks
            active = data.get("active", True)
            secret_key = data.get("secret_key")

            if not name or not url:
                return jsonify({"success": False, "error": "name and url required"}), 400

            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                """
                INSERT INTO health_alert_webhooks
                (name, url, event_types, stack_filter, active, secret_key)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (name, url, event_types, stack_filter, active, secret_key),
            )

            result = cur.fetchone()
            conn.commit()

            return jsonify({"success": True, "id": result[0]})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/health/webhooks/<int:webhook_id>", methods=["DELETE"])
    @login_required
    @admin_required_decorator
    def delete_health_webhook(webhook_id):
        """Delete a health alert webhook"""
        try:
            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                "DELETE FROM health_alert_webhooks WHERE id = ?",
                (webhook_id,),
            )
            result = cur.fetchone()
            conn.commit()

            if result:
                return jsonify({"success": True, "message": "Webhook deleted"})
            else:
                return jsonify({"success": False, "error": "Webhook not found"}), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/health/webhooks/<int:webhook_id>/test", methods=["POST"])
    @login_required
    @admin_required_decorator
    def test_health_webhook(webhook_id):
        """Test a health alert webhook"""
        import requests as req_lib

        try:
            conn = get_db()
            cur = conn.cursor()

            cur.execute("SELECT * FROM health_alert_webhooks WHERE id = ?", (webhook_id,))
            webhook = cur.fetchone()

            if not webhook:
                cur.close()
                conn.close()
                return jsonify({"success": False, "error": "Webhook not found"}), 404

            # Send test payload
            payload = {
                "event_type": "test",
                "message": "Test alert from A.R.C.H.I.E. Health Monitoring",
                "timestamp": datetime.utcnow().isoformat(),
                "test": True,
            }

            headers = {"Content-Type": "application/json"}
            if webhook.get("secret_key"):
                headers["X-Webhook-Secret"] = webhook["secret_key"]

            try:
                response = req_lib.post(webhook["url"], json=payload, headers=headers, timeout=10)
                status_code = response.status_code
                success = 200 <= status_code < 300
                error_msg = None if success else response.text[:500]
            except req_lib.RequestException as e:
                status_code = 0
                success = False
                error_msg = str(e)

            # Update webhook status
            cur.execute(
                """
                UPDATE health_alert_webhooks
                SET last_triggered = CURRENT_TIMESTAMP,
                    last_status = ?,
                    last_error = ?
                WHERE id = ?
            """,
                (status_code, error_msg, webhook_id),
            )
            conn.commit()

            return jsonify({"success": success, "status_code": status_code, "error": error_msg})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/system-health-full")
    @login_required
    @admin_required_decorator
    def api_system_health_full():
        """Full system health check (moved from Blueprint/Mission Control)."""
        if not BLUEPRINT_HEALTH_AVAILABLE:
            return jsonify({"error": "Health check not available"}), 500
        try:
            health = get_system_health()
            return jsonify(health)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
