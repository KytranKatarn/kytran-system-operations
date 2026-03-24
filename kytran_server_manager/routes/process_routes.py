"""Process & Service Management Routes"""

from flask import jsonify, request
from flask_login import login_required, current_user
from ..helpers import get_db, audit_log, load_host_monitor_data
from ..services.system_service import get_system_service



def register_process_routes(bp, admin_required_decorator):
    @bp.route("/api/processes")
    @login_required
    @admin_required_decorator
    def api_processes():
        """Get running processes - prefers host data over container psutil"""
        try:
            sort_by = request.args.get("sort", "cpu")
            limit = int(request.args.get("limit", 50))

            # Try host monitor data first (shows real host processes)
            host_data, host_age = load_host_monitor_data()
            if host_data and host_data.get("processes"):
                processes = host_data["processes"]
                # Mark as host processes
                for p in processes:
                    p["from_host"] = True

                # Sort
                if sort_by == "cpu":
                    processes.sort(key=lambda x: x.get("cpu_percent", 0), reverse=True)
                elif sort_by == "memory":
                    processes.sort(key=lambda x: x.get("memory_percent", 0), reverse=True)
                elif sort_by == "name":
                    processes.sort(key=lambda x: (x.get("name") or "").lower())
                elif sort_by == "category":
                    # Sort by category: docker first, then user, system, kernel
                    category_order = {"docker": 0, "user": 1, "system": 2, "kernel": 3}
                    processes.sort(
                        key=lambda x: (
                            category_order.get(x.get("category", "user"), 1),
                            -(x.get("cpu_percent", 0)),
                        )
                    )

                return jsonify(
                    {
                        "success": True,
                        "data": processes[:limit],
                        "count": len(processes),
                        "source": "host",
                        "data_age": int(host_age) if host_age else None,
                    }
                )

            # Fall back to container psutil
            service = get_system_service()
            data = service.get_process_list(sort_by=sort_by, limit=limit)
            return jsonify({"success": True, "data": data, "count": len(data), "source": "container"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/services")
    @login_required
    @admin_required_decorator
    def api_services():
        """Get systemd services"""
        try:
            filter_type = request.args.get("filter", "all")

            service = get_system_service()
            data = service.get_services_list(filter_type=filter_type)
            return jsonify({"success": True, "data": data, "count": len(data)})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/process/<int:pid>/kill", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_kill_process(pid):
        """Kill a process"""
        try:
            # Reject system-critical PIDs
            if pid <= 2:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Cannot kill system-critical processes (PID 0-2)",
                        }
                    ),
                    403,
                )

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

            signal = data.get("signal", "SIGTERM")
            service = get_system_service()
            result = service.kill_process(pid, signal)

            # Audit log
            audit_id = audit_log(
                action_type="process_kill",
                target=str(pid),
                details={"signal": signal},
                success=result["success"],
                error_message=result.get("error"),
            )

            result["audit_id"] = audit_id
            return jsonify(result)
        except Exception as e:
            audit_log("process_kill", str(pid), success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/service/<service_name>/action", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_service_action(service_name):
        """Perform action on a systemd service"""
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
            result = service.service_action(service_name, action)

            # Audit log
            audit_id = audit_log(
                action_type="service_action",
                target=service_name,
                details={"action": action},
                success=result["success"],
                error_message=result.get("error"),
            )

            result["audit_id"] = audit_id
            return jsonify(result)
        except Exception as e:
            audit_log("service_action", service_name, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500
