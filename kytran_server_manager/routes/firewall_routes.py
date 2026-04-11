"""
System Operations — Firewall Routes
======================================
Extracted from routes.py during ADR-045 route split refactor.

Endpoints: firewall status, rules, enable, disable, allow, deny, update, delete
Helper: parse_ufw_rules
"""

from flask import jsonify, request
from flask_login import login_required, current_user

from .host_command_client import (
    submit_and_wait,
    HostCommandTimeout,
    HostCommandQueueUnavailable,
)
from .helpers import audit_log, require_reauth


def parse_ufw_rules(output):
    """Parse UFW numbered output into structured data.

    Example input:
    Status: active
         To                         Action      From
         --                         ------      ----
    [ 1] 22/tcp                     ALLOW IN    Anywhere
    [ 2] 80/tcp                     ALLOW IN    Anywhere
    [ 3] 443/tcp                    ALLOW IN    Anywhere

    Returns list of dicts with: number, port, action, direction, from_addr
    """
    import re

    rules = []
    for line in output.split("\n"):
        # Match lines like: [ 1] 22/tcp                     ALLOW IN    Anywhere
        match = re.match(r"\[\s*(\d+)\]\s+(\S+)\s+(ALLOW|DENY|REJECT|LIMIT)\s*(IN|OUT)?\s*(.*)", line)
        if match:
            rule_num = int(match.group(1))
            port_proto = match.group(2)
            action = match.group(3)
            direction = match.group(4) or "IN"
            from_addr = match.group(5).strip() or "Anywhere"

            rules.append(
                {
                    "number": rule_num,
                    "port": port_proto,
                    "action": action,
                    "direction": direction,
                    "from": from_addr,
                }
            )
    return rules


def register_firewall_routes(bp, admin_required_decorator):
    """Register firewall-related routes on the given blueprint."""

    @bp.route("/api/firewall/status")
    @login_required
    @admin_required_decorator
    def api_firewall_status():
        """Get UFW firewall status"""
        try:
            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait("firewall_status", {}, timeout=30, submitted_by=username, user_id=user_id)
            cmd_result = result_data.get("result", {})

            if cmd_result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "active": cmd_result.get("active", False),
                        "output": cmd_result.get("output", ""),
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": cmd_result.get("error", "Failed to get status"),
                        }
                    ),
                    500,
                )

        except HostCommandQueueUnavailable as e:
            return (
                jsonify({"success": False, "error": str(e), "queue_unavailable": True}),
                503,
            )
        except HostCommandTimeout as e:
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/firewall/rules")
    @login_required
    @admin_required_decorator
    def api_firewall_rules():
        """Get numbered list of UFW firewall rules (works even when inactive)"""
        try:
            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait("firewall_rules", {}, timeout=30, submitted_by=username, user_id=user_id)
            cmd_result = result_data.get("result", {})

            if cmd_result.get("success"):
                output = cmd_result.get("output", "")
                from_config = cmd_result.get("from_config", False)

                # If rules came from config (UFW inactive), use those directly
                if from_config and cmd_result.get("rules"):
                    rules = cmd_result.get("rules", [])
                else:
                    # Parse from ufw status numbered output
                    rules = parse_ufw_rules(output)

                return jsonify(
                    {
                        "success": True,
                        "rules": rules,
                        "raw_output": output,
                        "from_config": from_config,
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": cmd_result.get("error", "Failed to get rules"),
                        }
                    ),
                    500,
                )

        except HostCommandQueueUnavailable as e:
            return (
                jsonify({"success": False, "error": str(e), "queue_unavailable": True}),
                503,
            )
        except HostCommandTimeout as e:
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/firewall/enable", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_firewall_enable():
        """Enable UFW firewall"""
        try:
            # Re-auth check for dangerous operation
            reauth_error = require_reauth()
            if reauth_error:
                return reauth_error

            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait("firewall_enable", {}, timeout=60, submitted_by=username, user_id=user_id)
            cmd_result = result_data.get("result", {})

            audit_log(
                action_type="firewall_enable",
                target="ufw",
                details={"command_id": result_data.get("command_id")},
                success=cmd_result.get("success", False),
                error_message=cmd_result.get("error"),
            )

            if cmd_result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "message": cmd_result.get("message", "Firewall enabled"),
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": cmd_result.get("error", "Failed to enable"),
                        }
                    ),
                    500,
                )

        except HostCommandQueueUnavailable as e:
            return (
                jsonify({"success": False, "error": str(e), "queue_unavailable": True}),
                503,
            )
        except HostCommandTimeout as e:
            audit_log("firewall_enable", "ufw", success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/firewall/disable", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_firewall_disable():
        """Disable UFW firewall"""
        try:
            # Re-auth check for dangerous operation
            reauth_error = require_reauth()
            if reauth_error:
                return reauth_error

            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait("firewall_disable", {}, timeout=60, submitted_by=username, user_id=user_id)
            cmd_result = result_data.get("result", {})

            audit_log(
                action_type="firewall_disable",
                target="ufw",
                details={"command_id": result_data.get("command_id")},
                success=cmd_result.get("success", False),
                error_message=cmd_result.get("error"),
            )

            if cmd_result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "message": cmd_result.get("message", "Firewall disabled"),
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": cmd_result.get("error", "Failed to disable"),
                        }
                    ),
                    500,
                )

        except HostCommandQueueUnavailable as e:
            return (
                jsonify({"success": False, "error": str(e), "queue_unavailable": True}),
                503,
            )
        except HostCommandTimeout as e:
            audit_log("firewall_disable", "ufw", success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/firewall/allow", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_firewall_allow():
        """Allow a port through the firewall"""
        try:
            data = request.get_json() or {}
            port = data.get("port")
            protocol = data.get("protocol", "tcp")

            # Validate port
            if not port or not isinstance(port, int) or not (1 <= port <= 65535):
                return (
                    jsonify({"success": False, "error": "Invalid port. Must be integer 1-65535"}),
                    400,
                )

            # Validate protocol
            if protocol not in ("tcp", "udp"):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Invalid protocol. Must be 'tcp' or 'udp'",
                        }
                    ),
                    400,
                )

            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait(
                "firewall_allow",
                {"port": port, "protocol": protocol},
                timeout=60,
                submitted_by=username,
                user_id=user_id,
            )
            cmd_result = result_data.get("result", {})

            audit_log(
                action_type="firewall_allow",
                target=f"{port}/{protocol}",
                details={
                    "port": port,
                    "protocol": protocol,
                    "command_id": result_data.get("command_id"),
                },
                success=cmd_result.get("success", False),
                error_message=cmd_result.get("error"),
            )

            if cmd_result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "message": cmd_result.get("message", f"Allowed {port}/{protocol}"),
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": cmd_result.get("error", "Failed to allow port"),
                        }
                    ),
                    500,
                )

        except HostCommandQueueUnavailable as e:
            return (
                jsonify({"success": False, "error": str(e), "queue_unavailable": True}),
                503,
            )
        except HostCommandTimeout as e:
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/firewall/deny", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_firewall_deny():
        """Deny a port through the firewall"""
        try:
            data = request.get_json() or {}
            port = data.get("port")
            protocol = data.get("protocol", "tcp")

            # Validate port
            if not port or not isinstance(port, int) or not (1 <= port <= 65535):
                return (
                    jsonify({"success": False, "error": "Invalid port. Must be integer 1-65535"}),
                    400,
                )

            # Validate protocol
            if protocol not in ("tcp", "udp"):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Invalid protocol. Must be 'tcp' or 'udp'",
                        }
                    ),
                    400,
                )

            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait(
                "firewall_deny",
                {"port": port, "protocol": protocol},
                timeout=60,
                submitted_by=username,
                user_id=user_id,
            )
            cmd_result = result_data.get("result", {})

            audit_log(
                action_type="firewall_deny",
                target=f"{port}/{protocol}",
                details={
                    "port": port,
                    "protocol": protocol,
                    "command_id": result_data.get("command_id"),
                },
                success=cmd_result.get("success", False),
                error_message=cmd_result.get("error"),
            )

            if cmd_result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "message": cmd_result.get("message", f"Denied {port}/{protocol}"),
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": cmd_result.get("error", "Failed to deny port"),
                        }
                    ),
                    500,
                )

        except HostCommandQueueUnavailable as e:
            return (
                jsonify({"success": False, "error": str(e), "queue_unavailable": True}),
                503,
            )
        except HostCommandTimeout as e:
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/firewall/update", methods=["PUT"])
    @login_required
    @admin_required_decorator
    def api_firewall_update():
        """Update an existing firewall rule"""
        try:
            data = request.get_json() or {}
            old_port = data.get("old_port")
            old_protocol = data.get("old_protocol", "tcp")
            new_port = data.get("new_port")
            new_protocol = data.get("new_protocol", "tcp")
            action = data.get("action", "allow")

            # Validate ports
            if not old_port or not isinstance(old_port, int) or not (1 <= old_port <= 65535):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Invalid old_port. Must be integer 1-65535",
                        }
                    ),
                    400,
                )
            if not new_port or not isinstance(new_port, int) or not (1 <= new_port <= 65535):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Invalid new_port. Must be integer 1-65535",
                        }
                    ),
                    400,
                )

            # Validate protocols
            if old_protocol not in ("tcp", "udp") or new_protocol not in ("tcp", "udp"):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Invalid protocol. Must be 'tcp' or 'udp'",
                        }
                    ),
                    400,
                )

            # Validate action
            if action not in ("allow", "deny"):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Invalid action. Must be 'allow' or 'deny'",
                        }
                    ),
                    400,
                )

            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait(
                "firewall_update",
                {
                    "old_port": old_port,
                    "old_protocol": old_protocol,
                    "new_port": new_port,
                    "new_protocol": new_protocol,
                    "action": action,
                },
                timeout=60,
                submitted_by=username,
                user_id=user_id,
            )
            cmd_result = result_data.get("result", {})

            audit_log(
                action_type="firewall_update",
                target=f"{old_port}/{old_protocol} -> {new_port}/{new_protocol}",
                details={
                    "old_port": old_port,
                    "old_protocol": old_protocol,
                    "new_port": new_port,
                    "new_protocol": new_protocol,
                    "action": action,
                    "command_id": result_data.get("command_id"),
                },
                success=cmd_result.get("success", False),
                error_message=cmd_result.get("error"),
            )

            if cmd_result.get("success"):
                return jsonify({"success": True, "message": cmd_result.get("message", "Rule updated")})
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": cmd_result.get("error", "Failed to update rule"),
                        }
                    ),
                    500,
                )

        except HostCommandQueueUnavailable as e:
            return (
                jsonify({"success": False, "error": str(e), "queue_unavailable": True}),
                503,
            )
        except HostCommandTimeout as e:
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/firewall/delete", methods=["DELETE"])
    @login_required
    @admin_required_decorator
    def api_firewall_delete():
        """Delete a firewall rule by number"""
        try:
            # Re-auth check for dangerous operation
            reauth_error = require_reauth()
            if reauth_error:
                return reauth_error

            data = request.get_json() or {}
            rule_number = data.get("rule_number")

            # Validate rule number
            if not rule_number or not isinstance(rule_number, int) or rule_number < 1:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Invalid rule_number. Must be integer >= 1",
                        }
                    ),
                    400,
                )

            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait(
                "firewall_delete",
                {"rule_number": rule_number},
                timeout=60,
                submitted_by=username,
                user_id=user_id,
            )
            cmd_result = result_data.get("result", {})

            audit_log(
                action_type="firewall_delete",
                target=f"rule #{rule_number}",
                details={
                    "rule_number": rule_number,
                    "command_id": result_data.get("command_id"),
                },
                success=cmd_result.get("success", False),
                error_message=cmd_result.get("error"),
            )

            if cmd_result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "message": cmd_result.get("message", f"Deleted rule #{rule_number}"),
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": cmd_result.get("error", "Failed to delete rule"),
                        }
                    ),
                    500,
                )

        except HostCommandQueueUnavailable as e:
            return (
                jsonify({"success": False, "error": str(e), "queue_unavailable": True}),
                503,
            )
        except HostCommandTimeout as e:
            audit_log(
                "firewall_delete",
                f"rule #{rule_number}",
                success=False,
                error_message=str(e),
            )
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
