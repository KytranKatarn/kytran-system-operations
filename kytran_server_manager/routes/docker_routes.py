"""Docker Management Routes"""

import os
import subprocess

from flask import jsonify, request, Response
from flask_login import login_required
from psycopg2.extras import RealDictCursor, Json
from .helpers import (
    BASE_DIR,
    load_host_monitor_data,
    get_db,
    audit_log,
    require_reauth,
)
from .system_service import get_system_service


def register_docker_routes(bp, admin_required_decorator):
    @bp.route("/api/docker")
    @login_required
    @admin_required_decorator
    def api_docker():
        """Get Docker containers"""
        try:
            service = get_system_service()
            data = service.get_docker_containers()
            return jsonify({"success": True, "data": data, "count": len(data)})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/docker/health")
    @login_required
    @admin_required_decorator
    def api_docker_health():
        """Get Docker container health status and resource usage"""
        try:
            host_data, host_age = load_host_monitor_data()
            if not host_data:
                return (
                    jsonify({"success": False, "error": "Host monitor data not available"}),
                    503,
                )

            docker_health = host_data.get("docker_health", [])

            # Aggregate by stack using compose_project label (preferred) or container name prefix (fallback)
            stacks = {}
            for container in docker_health:
                name = container.get("name", "")
                # Use compose_project label if available, otherwise fall back to name prefix
                stack_name = container.get("compose_project", "")
                if not stack_name:
                    # Fallback: extract prefix before first underscore
                    stack_name = name.split("_")[0] if "_" in name else name

                if stack_name not in stacks:
                    stacks[stack_name] = {
                        "name": stack_name,
                        "containers": [],
                        "total_cpu": 0.0,
                        "total_mem": 0.0,
                        "health_summary": "healthy",
                        "restart_total": 0,
                    }

                stacks[stack_name]["containers"].append(container)
                stacks[stack_name]["total_cpu"] += container.get("cpu_percent", 0)
                stacks[stack_name]["total_mem"] += container.get("mem_percent", 0)
                stacks[stack_name]["restart_total"] += container.get("restart_count", 0)

                # Update health summary (worst status wins)
                health = container.get("health", "none")
                current = stacks[stack_name]["health_summary"]
                if health == "unhealthy":
                    stacks[stack_name]["health_summary"] = "unhealthy"
                elif health == "starting" and current != "unhealthy":
                    stacks[stack_name]["health_summary"] = "starting"
                elif container.get("state") != "running" and current not in [
                    "unhealthy",
                    "starting",
                ]:
                    stacks[stack_name]["health_summary"] = "degraded"

            return jsonify(
                {
                    "success": True,
                    "containers": docker_health,
                    "stacks": list(stacks.values()),
                    "data_age": int(host_age) if host_age else None,
                }
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/stacks/<stack_name>/web-ui-ports", methods=["PUT"])
    @login_required
    @admin_required_decorator
    def api_update_web_ui_ports(stack_name):
        """Update web UI port definitions for a stack"""
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "SELECT id, name, is_system FROM docker_stacks WHERE name = %s",
                (stack_name,),
            )
            stack = cur.fetchone()
            if not stack:
                cur.close()
                conn.close()
                return jsonify({"success": False, "error": "Stack not found"}), 404

            # System stack requires re-auth
            if stack["is_system"]:
                reauth = require_reauth()
                if reauth is not None:
                    cur.close()
                    conn.close()
                    return reauth

            data = request.get_json() or {}
            web_ui_ports = data.get("web_ui_ports", [])

            # Validate structure
            if not isinstance(web_ui_ports, list):
                cur.close()
                conn.close()
                return (
                    jsonify({"success": False, "error": "web_ui_ports must be an array"}),
                    400,
                )

            for entry in web_ui_ports:
                if not isinstance(entry, dict) or "port" not in entry:
                    cur.close()
                    conn.close()
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": "Each entry must have a 'port' field",
                            }
                        ),
                        400,
                    )
                if not isinstance(entry["port"], int):
                    cur.close()
                    conn.close()
                    return (
                        jsonify({"success": False, "error": "Port must be an integer"}),
                        400,
                    )

            cur.execute(
                "UPDATE docker_stacks SET web_ui_ports = %s WHERE name = %s",
                (Json(web_ui_ports), stack_name),
            )
            conn.commit()

            audit_log("update_web_ui_ports", stack_name, {"web_ui_ports": web_ui_ports})

            return jsonify({"success": True, "web_ui_ports": web_ui_ports})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/docker/<container_id>/action", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_docker_action(container_id):
        """Perform action on a Docker container"""
        try:
            data = request.get_json() or {}

            # Require confirmation
            if not data.get("confirm"):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Confirmation required",
                            "requires_confirm": True,
                        }
                    ),
                    400,
                )

            action = data.get("action", "restart")
            service = get_system_service()
            result = service.docker_action(container_id, action)

            # Audit log
            audit_id = audit_log(
                action_type="docker_action",
                target=container_id,
                details={"action": action},
                success=result["success"],
                error_message=result.get("error"),
            )

            result["audit_id"] = audit_id
            return jsonify(result)
        except Exception as e:
            audit_log("docker_action", container_id, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/docker/compose", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_docker_compose():
        """Execute docker-compose commands"""
        try:
            data = request.get_json() or {}

            if not data.get("confirm"):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Confirmation required",
                            "requires_confirm": True,
                        }
                    ),
                    400,
                )

            action = data.get("action", "up")
            allowed_actions = ["up", "down", "restart", "pull"]
            if action not in allowed_actions:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"Invalid action. Allowed: {allowed_actions}",
                        }
                    ),
                    400,
                )

            compose_file = os.path.join(BASE_DIR, "docker-compose.yml")

            cmd_map = {
                "up": ["docker-compose", "-f", compose_file, "up", "-d"],
                "down": ["docker-compose", "-f", compose_file, "down"],
                "restart": ["docker-compose", "-f", compose_file, "restart"],
                "pull": ["docker-compose", "-f", compose_file, "pull"],
            }

            result = subprocess.run(cmd_map[action], capture_output=True, text=True, timeout=120)

            success = result.returncode == 0
            audit_log(
                action_type="docker_compose",
                target=action,
                details={"stdout": result.stdout[:500], "stderr": result.stderr[:500]},
                success=success,
                error_message=result.stderr if not success else None,
            )

            return jsonify(
                {
                    "success": success,
                    "output": result.stdout,
                    "error": result.stderr if not success else None,
                }
            )
        except subprocess.TimeoutExpired:
            return jsonify({"success": False, "error": "Command timed out"}), 500
        except Exception as e:
            audit_log("docker_compose", "error", success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/docker/<container_id>/logs", methods=["GET"])
    @login_required
    @admin_required_decorator
    def api_docker_logs(container_id):
        """Get container logs"""
        try:
            tail = request.args.get("tail", "100")

            result = subprocess.run(
                ["docker", "logs", "--tail", tail, container_id],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Return as plain text
            logs = result.stdout + result.stderr
            return Response(logs, mimetype="text/plain")
        except Exception as e:
            return f"Error fetching logs: {str(e)}", 500
