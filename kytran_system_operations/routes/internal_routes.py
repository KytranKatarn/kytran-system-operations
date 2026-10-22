"""Machine-to-machine endpoints for ARCHIE platform integrations."""

import hmac
import os
import re

from flask import jsonify, request

from ..services.host_command_client import get_queue_status, get_result, submit_host_command
from .helpers import audit_log


def _authorized():
    expected = os.environ.get("KSO_PLATFORM_API_KEY", "").strip()
    supplied = request.headers.get("X-KSO-Internal-Key", "").strip()
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def register_internal_routes(app):
    @app.before_request
    def _guard_kso_internal_api():
        if request.path.startswith("/api/internal/kso/") and not _authorized():
            return jsonify({"success": False, "error": "Unauthorized"}), 401

    @app.get("/api/internal/kso/host-commands/status")
    def host_command_status():
        return jsonify({"success": True, **get_queue_status()})

    @app.post("/api/internal/kso/host-commands")
    def host_command_submit():
        data = request.get_json(silent=True) or {}
        command_type = data.get("command_type")
        params = data.get("params", {})
        if not isinstance(command_type, str) or not command_type.strip():
            return jsonify({"success": False, "error": "command_type is required"}), 400
        if not isinstance(params, dict):
            return jsonify({"success": False, "error": "params must be an object"}), 400
        try:
            command_id = submit_host_command(
                command_type,
                params,
                submitted_by=str(data.get("submitted_by") or "archie-platform")[:120],
                user_id=data.get("user_id"),
            )
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 503
        audit_log(
            action_type=f"platform_host_command_{command_type}",
            target=command_type,
            details={
                "command_id": command_id,
                "submitted_by": str(data.get("submitted_by") or "archie-platform")[:120],
                "platform_user_id": data.get("user_id"),
            },
        )
        return jsonify({"success": True, "command_id": command_id}), 202

    @app.get("/api/internal/kso/host-commands/<command_id>")
    def host_command_result(command_id):
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", command_id):
            return jsonify({"success": False, "error": "Invalid command ID"}), 400
        result = get_result(command_id)
        if result is not None:
            return jsonify({"success": True, "status": "completed", "data": result})
        return jsonify({"success": True, "status": "pending"}), 202

    @app.get("/api/internal/kso/system-overview")
    def system_overview():
        from ..system_service import get_system_service

        return jsonify({"success": True, "data": get_system_service().get_overview()})
