"""
System Operations — Proxy Routes
Host selector + read-only proxy to remote nodes.
Phase 6: Starship Integration.
"""

import hashlib
import hmac
import logging
import time as time_mod

import requests
from flask import jsonify, request
from flask_login import login_required

logger = logging.getLogger(__name__)

ALLOWED_ENDPOINTS = {
    "overview",
    "cpu",
    "memory",
    "disks",
    "docker",
    "network",
    "processes",
    "services",
    "node-health",
}
MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5 MB
PROXY_TIMEOUT = 30


def register_proxy_routes(bp, admin_required_decorator):
    """Register host-selector and proxy routes on the system_operations blueprint."""

    @bp.route("/api/hosts")
    @login_required
    @admin_required_decorator
    def api_list_hosts():
        """List hub + all active approved nodes for host selector dropdown."""
        from database import get_db_cursor

        hosts = [{"id": "hub", "name": "Hub (local)", "hostname": "localhost", "is_hub": True}]
        try:
            with get_db_cursor() as (conn, cur):
                cur.execute(
                    """
                    SELECT node_id, node_name, hostname, port, is_active,
                           last_heartbeat, gpu_model, ram_gb
                    FROM remote_nodes
                    WHERE is_active = true AND approval_status = 'approved'
                    ORDER BY node_name
                    """
                )
                for row in cur.fetchall():
                    hosts.append(
                        {
                            "id": row["node_id"],
                            "name": row["node_name"],
                            "hostname": row["hostname"],
                            "port": row["port"],
                            "is_hub": False,
                            "gpu_model": row["gpu_model"],
                            "ram_gb": row["ram_gb"],
                        }
                    )
        except Exception:
            logger.exception("Failed to fetch remote_nodes for host list")
        return jsonify({"success": True, "hosts": hosts})

    @bp.route("/api/proxy/<node_id>/<endpoint>")
    @login_required
    @admin_required_decorator
    def api_proxy_to_node(node_id, endpoint):
        """Proxy read-only API calls to a remote node."""
        # Validate endpoint against allowlist
        if endpoint not in ALLOWED_ENDPOINTS:
            return (
                jsonify({"success": False, "error": f"Endpoint '{endpoint}' not allowed"}),
                403,
            )

        # Look up node
        from database import get_db_cursor

        try:
            with get_db_cursor() as (conn, cur):
                cur.execute(
                    "SELECT hostname, port, auth_token FROM remote_nodes " "WHERE node_id = %s AND is_active = true",
                    (node_id,),
                )
                node = cur.fetchone()
                if not node:
                    return (
                        jsonify({"success": False, "error": "Node not found"}),
                        404,
                    )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

        # Build signed request
        hostname = node["hostname"]
        port = node["port"] or 3000
        auth_token = node.get("auth_token", "") or ""

        timestamp = str(int(time_mod.time()))
        sign_payload = f"{timestamp}:{node_id}:{endpoint}"
        signature = hmac.new(auth_token.encode(), sign_payload.encode(), hashlib.sha256).hexdigest()

        target_url = f"http://{hostname}:{port}/tools/system-operations/api/{endpoint}"

        try:
            resp = requests.get(
                target_url,
                headers={
                    "X-Hub-Signature": signature,
                    "X-Hub-Timestamp": timestamp,
                    "X-Hub-Node-Id": node_id,
                },
                timeout=PROXY_TIMEOUT,
                stream=True,
            )

            # Enforce response size limit
            content = resp.content[:MAX_RESPONSE_SIZE]

            return (
                content,
                resp.status_code,
                {"Content-Type": resp.headers.get("Content-Type", "application/json")},
            )
        except requests.Timeout:
            return (
                jsonify({"success": False, "error": "Node request timed out"}),
                504,
            )
        except requests.ConnectionError:
            return (
                jsonify({"success": False, "error": "Cannot reach node"}),
                502,
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
