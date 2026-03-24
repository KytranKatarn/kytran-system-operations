# TODO: Phase 5.5 — Convert PostgreSQL SQL (%s, NOW(), INTERVAL, RETURNING) to SQLite syntax
"""
Core Routes — Dashboard, overview, CPU, memory, hardware, history, host commands, audit log.
Extracted from platform routes.py for standalone deployment.
"""

import os
import json as json_lib
import time as time_mod
from datetime import datetime

from flask import render_template, jsonify, request
from flask_login import login_required, current_user

from ..helpers import (
    BASE_DIR,
    HOST_DATA_FILE,
    load_host_monitor_data,
    get_db,
    audit_log,
    record_metric,
    require_reauth,
    parse_compose_host_port,
    find_compose_file,
)
from ..services.system_service import get_system_service
from ..services.host_command_client import (
    submit_host_command,
    submit_and_wait,
    get_queue_status,
    get_result as get_command_result,
    HostCommandTimeout,
    HostCommandQueueUnavailable,
)


def register_core_routes(bp, admin_required_decorator):
    # ========================================================================
    # PAGE ROUTES
    # ========================================================================

    @bp.route("/")
    @login_required
    @admin_required_decorator
    def index():
        """Main Server Manager dashboard"""
        return render_template("dashboard.html")

    # ========================================================================
    # READ-ONLY API ENDPOINTS
    # ========================================================================

    @bp.route("/api/overview")
    @login_required
    @admin_required_decorator
    def api_overview():
        """Get complete system overview - merges container metrics with host monitor data"""
        try:
            service = get_system_service()
            data = service.get_overview()

            # Record metrics for history
            record_metric("cpu", data["cpu"]["usage_percent"])
            record_metric("cpu_0", data["cpu"]["usage_percent"])
            record_metric("memory", data["memory"]["usage_percent"])

            # Merge host monitor data for accurate host-level info
            host_data, host_age = load_host_monitor_data()
            if host_data:
                data["host_data_age"] = int(host_age) if host_age else None
                data["host_data_stale"] = host_age > 300 if host_age else True

                # System info: prefer host data over container psutil
                host_sys = host_data.get("system")
                if host_sys:
                    data["system"] = {
                        "hostname": host_sys.get("hostname", data["system"].get("hostname", "--")),
                        "distribution": host_sys.get("distribution", data["system"].get("distribution", "--")),
                        "release": host_sys.get("kernel", data["system"].get("release", "--")),
                        "architecture": host_sys.get("architecture", data["system"].get("architecture", "--")),
                        "uptime_seconds": host_sys.get("uptime_seconds", data["system"].get("uptime_seconds", 0)),
                        "boot_time": host_sys.get("boot_time", ""),
                        "process_count": host_sys.get("process_count", data["system"].get("process_count", 0)),
                        "users_logged_in": host_sys.get("users_logged_in", 0),
                        "load_average": host_sys.get("load_average"),
                    }

                # CPU: keep real-time usage from psutil, overlay host model/cores/temp
                host_cpu = host_data.get("cpu")
                if host_cpu:
                    data["cpu"]["model"] = host_cpu.get("model", data["cpu"].get("model"))
                    data["cpu"]["physical_cores"] = host_cpu.get("physical_cores")
                    data["cpu"]["logical_cores"] = host_cpu.get("logical_cores")
                    if host_cpu.get("temperature") is not None:
                        data["cpu"]["temperature"] = host_cpu["temperature"]
                    if host_cpu.get("temperatures"):
                        data["cpu"]["core_temperatures"] = host_cpu["temperatures"]
                    host_load = host_sys.get("load_average") if host_sys else None
                    if host_load:
                        data["cpu"]["load_avg"] = host_load

                # GPU: use host data (container can't see GPU) - supports multiple GPUs
                host_gpus = host_data.get("gpu")
                if host_gpus and len(host_gpus) > 0:
                    gpus_array = []
                    for idx, gpu in enumerate(host_gpus):
                        vram_total = gpu.get("vram_total_mb")
                        vram_used = gpu.get("vram_used_mb")
                        vram_pct = None
                        if vram_total and vram_used is not None and vram_total > 0:
                            vram_pct = round((vram_used / vram_total) * 100, 1)

                        gpu_data = {
                            "index": idx,
                            "available": True,
                            "model": gpu.get("model", "Unknown"),
                            "vendor": gpu.get("vendor"),
                            "temperature": gpu.get("temperature"),
                            "vram_total_mb": vram_total,
                            "vram_used_mb": vram_used,
                            "vram_percent": vram_pct,
                            "usage_percent": gpu.get("utilization_percent"),
                            "driver": gpu.get("driver"),
                            "has_detailed_stats": vram_total is not None,
                        }
                        gpus_array.append(gpu_data)
                        if gpu_data["usage_percent"] is not None:
                            record_metric(f"gpu_{idx}", gpu_data["usage_percent"])
                            if idx == 0:
                                record_metric("gpu", gpu_data["usage_percent"])
                        if vram_pct is not None:
                            record_metric(f"gpu_vram_{idx}", vram_pct)
                            if idx == 0:
                                record_metric("gpu_vram", vram_pct)

                    data["gpus"] = gpus_array
                    data["gpu"] = gpus_array[0]

                # Network: use host primary IP
                host_net = host_data.get("network")
                if host_net and host_net.get("primary_ip"):
                    data["network"]["primary_ip"] = host_net["primary_ip"]
                    data["network"]["host_hostname"] = host_net.get("hostname")

                # Disks: add host disk summary for dashboard
                host_disks = host_data.get("disks")
                if host_disks:
                    data["host_disks"] = host_disks

            return jsonify({"success": True, "data": data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/cpu")
    @login_required
    @admin_required_decorator
    def api_cpu():
        """Get CPU details"""
        try:
            service = get_system_service()
            data = service.get_cpu_info()
            return jsonify({"success": True, "data": data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/memory")
    @login_required
    @admin_required_decorator
    def api_memory():
        """Get memory details"""
        try:
            service = get_system_service()
            data = service.get_memory_info()
            return jsonify({"success": True, "data": data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/memory-hardware")
    @login_required
    @admin_required_decorator
    def api_memory_hardware():
        """Get physical memory hardware (DIMM) info from host monitor"""
        try:
            host_data, host_age = load_host_monitor_data()
            if host_data and host_data.get("memory_hardware"):
                mem_hw = host_data["memory_hardware"]
                return jsonify(
                    {
                        "success": True,
                        "data": mem_hw,
                        "source": "host",
                        "data_age": int(host_age) if host_age else None,
                    }
                )
            else:
                return jsonify(
                    {
                        "success": False,
                        "error": "Memory hardware info not available. Host monitor may need to be restarted.",
                        "data": None,
                    }
                )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/hardware")
    @login_required
    @admin_required_decorator
    def api_hardware():
        """Get comprehensive hardware info with upgrade potential"""
        try:
            host_data, host_age = load_host_monitor_data()
            if not host_data:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Host monitor data not available. Ensure host_monitor.py is running.",
                        }
                    ),
                    503,
                )

            cpu_hardware = host_data.get("cpu_hardware", {})
            cpu_info = host_data.get("cpu", {})
            motherboard = host_data.get("motherboard", {})
            memory_hardware = host_data.get("memory_hardware", {})
            gpu = host_data.get("gpu", [])
            pci_slots = host_data.get("pci_slots", [])
            sata_ports = host_data.get("sata_ports", {})

            if cpu_info.get("model") and not cpu_hardware.get("processors"):
                cpu_hardware["model"] = cpu_info.get("model")
            if cpu_info.get("physical_cores"):
                cpu_hardware["physical_cores"] = cpu_info.get("physical_cores")
            if cpu_info.get("logical_cores"):
                cpu_hardware["logical_cores"] = cpu_info.get("logical_cores")

            upgrade_summary = _compute_upgrade_summary(cpu_hardware, memory_hardware, pci_slots, sata_ports)
            memory_config = _analyze_memory_config(memory_hardware)

            result = {
                "cpu": cpu_hardware,
                "motherboard": motherboard,
                "memory": memory_hardware,
                "memory_config": memory_config,
                "gpu": gpu if gpu else [],
                "pci_slots": pci_slots,
                "sata_ports": sata_ports,
                "upgrade_summary": upgrade_summary,
            }

            return jsonify(
                {
                    "success": True,
                    "data": result,
                    "source": "host",
                    "data_age": int(host_age) if host_age else None,
                }
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/disks")
    @login_required
    @admin_required_decorator
    def api_disks():
        """Get disk information"""
        try:
            service = get_system_service()
            data = service.get_disk_info()
            return jsonify({"success": True, "data": data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/history")
    @login_required
    @admin_required_decorator
    def api_history():
        """Get historical metrics for graphs"""
        try:
            metric_type = request.args.get("type", "cpu")
            hours = int(request.args.get("hours", 1))
            hours = min(max(hours, 1), 720)

            conn = get_db()
            cur = conn.cursor()

            # TODO: Phase 5.5 — SQLite datetime functions differ from PostgreSQL
            if hours > 24:
                cur.execute(
                    """
                    SELECT AVG(value) as value, strftime('%Y-%m-%d %H:00:00', recorded_at) as recorded_at
                    FROM system_metrics_history
                    WHERE metric_type = ?
                      AND recorded_at > datetime('now', ? || ' hours')
                    GROUP BY strftime('%Y-%m-%d %H:00:00', recorded_at)
                    ORDER BY recorded_at ASC
                    LIMIT 720
                """,
                    (metric_type, str(-hours)),
                )
            else:
                cur.execute(
                    """
                    SELECT value, recorded_at
                    FROM system_metrics_history
                    WHERE metric_type = ?
                      AND recorded_at > datetime('now', ? || ' hours')
                    ORDER BY recorded_at ASC
                    LIMIT 720
                """,
                    (metric_type, str(-hours)),
                )

            data = cur.fetchall()
            labels = [row[1] if isinstance(row[1], str) else str(row[1]) for row in data]
            values = [round(float(row[0]), 1) for row in data]

            conn.close()

            return jsonify(
                {
                    "success": True,
                    "data": {
                        "labels": labels,
                        "values": values,
                        "metric_type": metric_type,
                        "count": len(values),
                        "hours": hours,
                    },
                }
            )
        except Exception as e:
            import traceback
            print(f"History API error: {e}")
            print(traceback.format_exc())
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/history/multi")
    @login_required
    @admin_required_decorator
    def api_history_multi():
        """Get historical metrics for multiple devices (e.g., gpu_0, gpu_1)"""
        try:
            base_type = request.args.get("type", "gpu")
            hours = int(request.args.get("hours", 1))
            hours = min(max(hours, 1), 720)

            conn = get_db()
            cur = conn.cursor()

            # Find all metric types matching pattern
            cur.execute(
                """
                SELECT DISTINCT metric_type
                FROM system_metrics_history
                WHERE metric_type GLOB ? || '_[0-9]*'
                  AND recorded_at > datetime('now', ? || ' hours')
                ORDER BY metric_type
            """,
                (base_type, str(-hours)),
            )
            device_types = [row[0] for row in cur.fetchall()]

            if not device_types:
                device_types = [base_type]

            devices = {}
            for metric_type in device_types:
                if hours > 24:
                    cur.execute(
                        """
                        SELECT AVG(value) as value, strftime('%Y-%m-%d %H:00:00', recorded_at) as recorded_at
                        FROM system_metrics_history
                        WHERE metric_type = ?
                          AND recorded_at > datetime('now', ? || ' hours')
                        GROUP BY strftime('%Y-%m-%d %H:00:00', recorded_at)
                        ORDER BY recorded_at ASC
                        LIMIT 720
                    """,
                        (metric_type, str(-hours)),
                    )
                else:
                    cur.execute(
                        """
                        SELECT value, recorded_at
                        FROM system_metrics_history
                        WHERE metric_type = ?
                          AND recorded_at > datetime('now', ? || ' hours')
                        ORDER BY recorded_at ASC
                        LIMIT 720
                    """,
                        (metric_type, str(-hours)),
                    )
                data = cur.fetchall()
                if data:
                    devices[metric_type] = {
                        "labels": [row[1] if isinstance(row[1], str) else str(row[1]) for row in data],
                        "values": [round(float(row[0]), 1) for row in data],
                    }

            conn.close()

            return jsonify(
                {
                    "success": True,
                    "data": {
                        "devices": devices,
                        "base_type": base_type,
                        "device_count": len(devices),
                    },
                }
            )
        except Exception as e:
            import traceback
            print(f"Multi-history API error: {e}")
            print(traceback.format_exc())
            return jsonify({"success": False, "error": str(e)}), 500

    # ========================================================================
    # HOST COMMAND QUEUE ENDPOINTS
    # ========================================================================

    DANGEROUS_COMMAND_TYPES = {
        "lvm_shrink",
        "disk_format",
        "partition_create",
        "pv_create_vg_extend",
        "vg_create",
        "convert_to_lvm",
        "lvm_snapshot",
        "disk_prepare",
        "disk_prepare_lvm",
        "disk_wipe",
    }

    @bp.route("/api/host-command/status")
    @login_required
    @admin_required_decorator
    def api_host_command_status():
        """Get host command queue health status"""
        try:
            status = get_queue_status()
            return jsonify({"success": True, **status})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/host-command/submit", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_host_command_submit():
        """Submit a command to the host command queue"""
        try:
            data = request.get_json() or {}
            command_type = data.get("command_type")
            params = data.get("params", {})
            confirm = data.get("confirm", False)

            if not command_type:
                return jsonify({"success": False, "error": "command_type is required"}), 400

            if not confirm:
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

            if command_type in DANGEROUS_COMMAND_TYPES:
                reauth = require_reauth()
                if reauth is not None:
                    return reauth

            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")
            command_id = submit_host_command(
                command_type,
                params,
                submitted_by=username,
                user_id=user_id,
            )

            audit_log(
                action_type=f"host_command_{command_type}",
                target=command_type,
                details={"command_id": command_id, "params": params},
                success=True,
            )

            return jsonify({"success": True, "command_id": command_id})

        except HostCommandQueueUnavailable as e:
            return (
                jsonify({"success": False, "error": str(e), "queue_unavailable": True}),
                503,
            )
        except Exception as e:
            audit_log(
                action_type="host_command_submit",
                target=data.get("command_type", "unknown"),
                success=False,
                error_message=str(e),
            )
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/host-command/<command_id>/result")
    @login_required
    @admin_required_decorator
    def api_host_command_result(command_id):
        """Poll for a host command result."""
        try:
            import re as re_mod

            if not re_mod.match(r"^[0-9a-f\-]{36}$", command_id):
                return (
                    jsonify({"success": False, "error": "Invalid command ID format"}),
                    400,
                )

            result = get_command_result(command_id)
            if result is not None:
                return jsonify({"success": True, "status": "completed", "data": result})
            else:
                pending_path = os.path.join(
                    os.environ.get("KSM_BASE_DIR", "/"),
                    "host_commands",
                    "pending",
                    f"{command_id}.json",
                )
                if os.path.exists(pending_path):
                    return jsonify({"success": True, "status": "pending"}), 202
                else:
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": "Command not found",
                                "status": "unknown",
                            }
                        ),
                        404,
                    )

        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ========================================================================
    # RE-AUTHENTICATION ENDPOINT
    # ========================================================================

    @bp.route("/api/auth/verify", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_auth_verify():
        """Verify user password for re-authentication of destructive operations"""
        try:
            import bcrypt
            from flask import session

            data = request.get_json() or {}
            password = data.get("password", "")

            if not password:
                return jsonify({"success": False, "error": "Password required"}), 400

            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT password_hash FROM users WHERE id = ?", (current_user.id,))
            user = cur.fetchone()
            conn.close()

            if not user:
                audit_log("reauth_attempt", "verify", success=False, error_message="User not found")
                return jsonify({"success": False, "error": "User not found"}), 404

            if not bcrypt.checkpw(password.encode(), user[0].encode()):
                audit_log(
                    "reauth_attempt",
                    "verify",
                    success=False,
                    error_message=f"Invalid password for user {current_user.username}",
                )
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"Invalid password for user '{current_user.username}'",
                        }
                    ),
                    401,
                )

            session["reauth_timestamp"] = time_mod.time()
            audit_log("reauth_attempt", "verify", success=True)

            return jsonify(
                {
                    "success": True,
                    "message": "Re-authentication successful",
                    "valid_for": 300,
                }
            )
        except Exception as e:
            audit_log("reauth_attempt", "verify", success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500

    # ========================================================================
    # AUDIT LOG ENDPOINT
    # ========================================================================

    @bp.route("/api/audit-log")
    @login_required
    @admin_required_decorator
    def api_audit_log():
        """Get system operations audit log"""
        try:
            limit = int(request.args.get("limit", 50))

            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, action_type, target, details, success,
                       error_message, ip_address, created_at, username
                FROM audit_log
                ORDER BY created_at DESC
                LIMIT ?
            """,
                (limit,),
            )

            rows = cur.fetchall()
            data = []
            for row in rows:
                data.append({
                    "id": row[0],
                    "action_type": row[1],
                    "target": row[2],
                    "details": json_lib.loads(row[3]) if row[3] else None,
                    "success": bool(row[4]),
                    "error_message": row[5],
                    "ip_address": row[6],
                    "created_at": row[7],
                    "username": row[8],
                })

            conn.close()
            return jsonify({"success": True, "data": data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ========================================================================
    # HOST DATA ENDPOINT
    # ========================================================================

    @bp.route("/api/host-data")
    @login_required
    @admin_required_decorator
    def api_host_data():
        """Get host system data from host monitor"""
        try:
            data, age = load_host_monitor_data()
            if data is None:
                return jsonify(
                    {
                        "success": False,
                        "error": "Host monitor data not available",
                        "hint": "Run host_monitor.py on the host machine",
                    }
                )

            data["data_age_seconds"] = int(age) if age else None
            data["is_stale"] = age > 300 if age else True

            return jsonify({"success": True, "data": data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ========================================================================
    # HEALTH CHECK
    # ========================================================================

    @bp.route("/health")
    def health_check():
        """Health check endpoint (no auth required)"""
        return jsonify({"healthy": True, "module": "kytran_server_manager"})


# ============================================================================
# Helper functions (module-level, used by routes above)
# ============================================================================


def _compute_upgrade_summary(cpu, memory, pci_slots, sata_ports=None):
    """Compute upgrade potential based on current hardware"""
    summary = {
        "memory": {
            "current_gb": 0, "max_gb": None, "expandable_gb": None,
            "empty_slots": 0, "total_slots": 0,
        },
        "pci": {"available_slots": 0, "total_slots": 0, "available_by_type": {}},
        "cpu": {
            "socket_type": None, "populated": 0, "max_sockets": 0, "can_add_cpu": False,
        },
        "sata": {"total_ports": 0, "used_ports": 0, "available_ports": 0},
    }

    if memory:
        summary["memory"]["current_gb"] = memory.get("total_capacity_gb", 0)
        summary["memory"]["max_gb"] = memory.get("max_capacity_gb")
        summary["memory"]["total_slots"] = memory.get("total_slots", 0)
        summary["memory"]["empty_slots"] = summary["memory"]["total_slots"] - memory.get("populated_slots", 0)
        if summary["memory"]["max_gb"] and summary["memory"]["current_gb"]:
            summary["memory"]["expandable_gb"] = summary["memory"]["max_gb"] - summary["memory"]["current_gb"]

    if pci_slots:
        summary["pci"]["total_slots"] = len(pci_slots)
        for slot in pci_slots:
            usage = slot.get("current_usage", "").lower()
            if usage in ("available", "empty"):
                summary["pci"]["available_slots"] += 1
                slot_type = slot.get("type", "Unknown")
                if "x16" in slot_type.lower():
                    key = "PCIe x16"
                elif "x8" in slot_type.lower():
                    key = "PCIe x8"
                elif "x4" in slot_type.lower():
                    key = "PCIe x4"
                elif "x1" in slot_type.lower():
                    key = "PCIe x1"
                elif "pci" in slot_type.lower() and "express" not in slot_type.lower():
                    key = "PCI"
                else:
                    key = slot_type[:20] if slot_type else "Unknown"
                summary["pci"]["available_by_type"][key] = summary["pci"]["available_by_type"].get(key, 0) + 1

    if cpu:
        socket_type = cpu.get("socket_type", "")
        summary["cpu"]["socket_type"] = socket_type
        summary["cpu"]["populated"] = cpu.get("populated_sockets", 0)
        summary["cpu"]["max_sockets"] = cpu.get("max_processors", 0)
        summary["cpu"]["can_add_cpu"] = (
            summary["cpu"]["max_sockets"] > summary["cpu"]["populated"] if summary["cpu"]["max_sockets"] else False
        )
        current_model = ""
        if cpu.get("processors"):
            current_model = cpu["processors"][0].get("model", "") if cpu["processors"] else ""
        cpu_upgrades = _get_cpu_upgrade_info(socket_type, current_model)
        summary["cpu"]["current_model"] = current_model
        summary["cpu"]["max_supported"] = cpu_upgrades.get("max_cpu")
        summary["cpu"]["upgrade_note"] = cpu_upgrades.get("note")
        summary["cpu"]["max_tdp"] = cpu_upgrades.get("max_tdp")

    if sata_ports:
        summary["sata"]["total_ports"] = sata_ports.get("total_ports", 0)
        summary["sata"]["used_ports"] = sata_ports.get("used_ports", 0)
        summary["sata"]["available_ports"] = sata_ports.get("available_ports", 0)

    summary["pci"]["gpu_slot_info"] = _get_gpu_slot_info(pci_slots)

    return summary


def _get_cpu_upgrade_info(socket_type, current_model):
    """Get CPU upgrade recommendations based on socket type"""
    info = {"max_cpu": None, "note": None, "max_tdp": None}
    socket_lower = (socket_type or "").lower()

    if "lga2011" in socket_lower and "v3" not in socket_lower:
        info["max_tdp"] = 150
        if "v2" in current_model.lower():
            info["max_cpu"] = "Xeon E5-2697 v2 (12C/24T, 2.7GHz)"
            info["note"] = "Ivy Bridge-EP. Top v2 Xeon for single-socket workstations."
        else:
            info["max_cpu"] = "Xeon E5-2690 (8C/16T, 2.9GHz) or upgrade to v2 series"
            info["note"] = "Sandy Bridge-EP. Consider v2 CPUs for more cores."
    elif "lga2011-3" in socket_lower or "lga2011 v3" in socket_lower:
        info["max_tdp"] = 145
        if "v4" in current_model.lower():
            info["max_cpu"] = "Xeon E5-2699 v4 (22C/44T, 2.2GHz)"
            info["note"] = "Broadwell-EP. Maximum cores available."
        else:
            info["max_cpu"] = "Xeon E5-2699 v3 (18C/36T, 2.3GHz) or v4 series"
            info["note"] = "Haswell-EP. Consider v4 for more cores."
    elif "lga1151" in socket_lower:
        info["max_tdp"] = 95
        info["max_cpu"] = "Core i9-9900K (8C/16T, 3.6GHz)"
        info["note"] = "Consumer socket. Limited to 8 cores max."
    elif "lga1200" in socket_lower:
        info["max_tdp"] = 125
        info["max_cpu"] = "Core i9-11900K (8C/16T, 3.5GHz)"
        info["note"] = "11th gen max. Consider LGA1700 for newer CPUs."
    elif "lga1700" in socket_lower:
        info["max_tdp"] = 253
        info["max_cpu"] = "Core i9-14900K (24C/32T, 3.2GHz)"
        info["note"] = "Latest Intel consumer socket."
    elif "am4" in socket_lower:
        info["max_tdp"] = 142
        info["max_cpu"] = "Ryzen 9 5950X (16C/32T, 3.4GHz)"
        info["note"] = "Check motherboard BIOS for Zen 3 support."
    elif "am5" in socket_lower:
        info["max_tdp"] = 170
        info["max_cpu"] = "Ryzen 9 9950X (16C/32T, 4.3GHz)"
        info["note"] = "Latest AMD consumer socket."

    return info


def _get_gpu_slot_info(pci_slots):
    """Get GPU slot upgrade information"""
    info = {
        "primary_slot": None,
        "max_length": 'Full-length (10.5")',
        "pcie_version": None,
        "lanes": None,
        "power_note": "Check PSU for GPU power connectors",
    }
    if not pci_slots:
        return info
    for slot in pci_slots:
        slot_type = slot.get("type", "").lower()
        if "x16" in slot_type and "pci express" in slot_type:
            info["primary_slot"] = slot.get("designation")
            info["lanes"] = 16
            if "3" in slot_type:
                info["pcie_version"] = "3.0"
                info["bandwidth"] = "~16 GB/s"
            elif "4" in slot_type:
                info["pcie_version"] = "4.0"
                info["bandwidth"] = "~32 GB/s"
            elif "5" in slot_type:
                info["pcie_version"] = "5.0"
                info["bandwidth"] = "~64 GB/s"
            else:
                info["pcie_version"] = "2.0"
                info["bandwidth"] = "~8 GB/s"
            if slot.get("current_usage", "").lower() == "in use":
                info["current_device"] = slot.get("device")
            break
    return info


def _analyze_memory_config(memory):
    """Analyze memory configuration for optimal setup recommendations"""
    config = {
        "current": {"populated": 0, "total_slots": 0, "capacity_gb": 0, "description": ""},
        "optimal": {"description": "", "recommendation": ""},
        "channel_mode": "Unknown",
    }
    if not memory:
        return config

    total_slots = memory.get("total_slots", 0)
    populated = memory.get("populated_slots", 0)
    capacity = memory.get("total_capacity_gb", 0)
    channels = memory.get("channels", "")

    config["current"]["populated"] = populated
    config["current"]["total_slots"] = total_slots
    config["current"]["capacity_gb"] = capacity
    config["current"]["description"] = f"{populated} of {total_slots} slots populated ({capacity} GB)"
    config["channel_mode"] = channels

    if total_slots == 8:
        if populated == 8:
            config["optimal"]["description"] = "8 of 8 (Quad Channel - Maximum)"
            config["optimal"]["recommendation"] = "Fully populated - optimal for quad-channel"
        elif populated == 4:
            config["optimal"]["description"] = "4 of 8 (Quad Channel)"
            config["optimal"]["recommendation"] = "Good for quad-channel. Add 4 more DIMMs for maximum capacity."
        elif populated < 4:
            config["optimal"]["description"] = "4 of 8 or 8 of 8 recommended"
            config["optimal"]["recommendation"] = f"Add {4 - populated} DIMMs for quad-channel, or {8 - populated} for maximum."
        else:
            config["optimal"]["description"] = "8 of 8 recommended"
            config["optimal"]["recommendation"] = f"Add {8 - populated} DIMMs for balanced quad-channel."
    elif total_slots == 4:
        if populated == 4:
            config["optimal"]["description"] = "4 of 4 (Dual Channel - Maximum)"
            config["optimal"]["recommendation"] = "Fully populated - optimal configuration"
        elif populated == 2:
            config["optimal"]["description"] = "2 of 4 (Dual Channel)"
            config["optimal"]["recommendation"] = "Good for dual-channel. Add 2 more DIMMs for maximum capacity."
        else:
            config["optimal"]["description"] = "2 of 4 or 4 of 4 recommended"
            config["optimal"]["recommendation"] = "Install DIMMs in pairs for dual-channel mode."
    elif total_slots == 2:
        if populated == 2:
            config["optimal"]["description"] = "2 of 2 (Dual Channel - Maximum)"
            config["optimal"]["recommendation"] = "Fully populated - optimal configuration"
        else:
            config["optimal"]["description"] = "2 of 2 recommended"
            config["optimal"]["recommendation"] = "Add 1 DIMM for dual-channel mode."
    else:
        config["optimal"]["description"] = f"{total_slots} of {total_slots}"
        config["optimal"]["recommendation"] = "Check motherboard manual for optimal configuration."

    return config
