# TODO: Phase 5.5 — Convert PostgreSQL SQL (%s, NOW(), INTERVAL, RETURNING) to SQLite syntax
"""Storage Management Routes"""

import os
import subprocess
from datetime import datetime

from flask import jsonify, request
from flask_login import login_required, current_user

from ..helpers import (
    load_host_monitor_data,
    get_db,
    audit_log,
)
from ..services.host_command_client import (
    submit_and_wait,
    HostCommandTimeout,
    HostCommandQueueUnavailable,
)
from ..services.system_service import get_system_service


def register_storage_routes(bp, admin_required_decorator):
    @bp.route("/api/disk/<path:device>/mount", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_mount_disk(device):
        """Mount a disk via host command queue"""
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

            mountpoint = data.get("mountpoint")
            if not mountpoint:
                return jsonify({"success": False, "error": "Mountpoint required"}), 400

            params = {"device": f"/dev/{device}", "mountpoint": mountpoint}
            if data.get("fstype"):
                params["fstype"] = data["fstype"]
            # Persist mount to fstab using UUID (prevents device letter shifting issues)
            params["persist"] = data.get("persist", True)

            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait("disk_mount", params, timeout=60, submitted_by=username, user_id=user_id)
            cmd_result = result_data.get("result", {})

            audit_log(
                action_type="disk_mount",
                target=device,
                details={
                    "mountpoint": mountpoint,
                    "command_id": result_data.get("command_id"),
                },
                success=cmd_result.get("success", False),
                error_message=cmd_result.get("error"),
            )

            if cmd_result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "message": cmd_result.get("message", f"Mounted /dev/{device} at {mountpoint}"),
                    }
                )
            else:
                return (
                    jsonify({"success": False, "error": cmd_result.get("error", "mount failed")}),
                    500,
                )

        except HostCommandQueueUnavailable as e:
            return (
                jsonify({"success": False, "error": str(e), "queue_unavailable": True}),
                503,
            )
        except HostCommandTimeout as e:
            audit_log("disk_mount", device, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            audit_log("disk_mount", device, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/disk/<path:device>/unmount", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_unmount_disk(device):
        """Unmount a disk via host command queue"""
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

            params = {"device": f"/dev/{device}"}
            if data.get("force"):
                params["force"] = True
            # Remove fstab entry when unmounting (prevents stale entries)
            params["remove_persist"] = data.get("remove_persist", True)

            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait("disk_unmount", params, timeout=60, submitted_by=username, user_id=user_id)
            cmd_result = result_data.get("result", {})

            audit_log(
                action_type="disk_unmount",
                target=device,
                details={"command_id": result_data.get("command_id")},
                success=cmd_result.get("success", False),
                error_message=cmd_result.get("error"),
            )

            if cmd_result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "message": cmd_result.get("message", f"Unmounted /dev/{device}"),
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": cmd_result.get("error", "umount failed"),
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
            audit_log("disk_unmount", device, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            audit_log("disk_unmount", device, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500

    # ============================================================================
    # LVM EXTEND ENDPOINT
    # ============================================================================

    @bp.route("/api/lvm/<vg_name>/<lv_name>/extend", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_extend_lvm(vg_name, lv_name):
        """Extend an LVM logical volume via host command queue"""
        try:
            import re

            # Validate LVM names — alphanumeric, underscore, hyphen only
            if not re.match(r"^[a-zA-Z0-9_\-]+$", vg_name):
                return (
                    jsonify({"success": False, "error": "Invalid volume group name"}),
                    400,
                )
            if not re.match(r"^[a-zA-Z0-9_\-]+$", lv_name):
                return (
                    jsonify({"success": False, "error": "Invalid logical volume name"}),
                    400,
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

            size_gb = data.get("size_gb")
            extend_all = data.get("extend_all", False)

            if not size_gb and not extend_all:
                return (
                    jsonify({"success": False, "error": "Must specify size_gb or extend_all"}),
                    400,
                )

            params = {"vg_name": vg_name, "lv_name": lv_name}
            if extend_all:
                params["extend_all"] = True
            elif size_gb:
                params["size_gb"] = size_gb

            user_id = current_user.id if current_user.is_authenticated else None
            username = getattr(current_user, "username", "unknown")

            result_data = submit_and_wait("lvm_extend", params, timeout=120, submitted_by=username, user_id=user_id)
            cmd_result = result_data.get("result", {})

            # Audit log
            audit_id = audit_log(
                action_type="lvm_extend",
                target=f"{vg_name}/{lv_name}",
                details={
                    "size_gb": size_gb,
                    "extend_all": extend_all,
                    "command_id": result_data.get("command_id"),
                },
                success=cmd_result.get("success", False),
                error_message=cmd_result.get("error"),
            )

            cmd_result["audit_id"] = audit_id
            return jsonify(cmd_result)

        except HostCommandQueueUnavailable:
            # Fallback to direct execution (container-side) if queue is unavailable
            try:
                service = get_system_service()
                result = service.extend_lvm(vg_name, lv_name, size_gb=size_gb, extend_all=extend_all)
                result["fallback"] = True
                audit_id = audit_log(
                    action_type="lvm_extend",
                    target=f"{vg_name}/{lv_name}",
                    details={
                        "size_gb": size_gb,
                        "extend_all": extend_all,
                        "fallback": True,
                    },
                    success=result["success"],
                    error_message=result.get("error"),
                )
                result["audit_id"] = audit_id
                return jsonify(result)
            except Exception as inner_e:
                audit_log(
                    "lvm_extend",
                    f"{vg_name}/{lv_name}",
                    success=False,
                    error_message=str(inner_e),
                )
                return jsonify({"success": False, "error": str(inner_e)}), 500
        except HostCommandTimeout as e:
            audit_log("lvm_extend", f"{vg_name}/{lv_name}", success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 504
        except Exception as e:
            audit_log("lvm_extend", f"{vg_name}/{lv_name}", success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/filesystem/resize", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_filesystem_resize():
        """Resize a filesystem to fill its container (partition/LV).

        Useful when lvextend succeeded but resize2fs failed (e.g., device was busy).
        """
        try:
            from ..services.host_command_client import (
                submit_and_wait,
                is_queue_available,
                HostCommandTimeout,
                HostCommandQueueUnavailable,
            )

            data = request.get_json() or {}
            device = data.get("device")

            if not device:
                return jsonify({"success": False, "error": "Device path required"}), 400

            # Validate device path format
            import re

            if not re.match(r"^/dev/[a-zA-Z0-9_/\-]+$", device):
                return jsonify({"success": False, "error": "Invalid device path"}), 400

            if not is_queue_available():
                return (
                    jsonify({"success": False, "error": "Host command queue not available"}),
                    503,
                )

            result_data = submit_and_wait("filesystem_resize", {"device": device}, timeout=120)
            result = result_data.get("result", {"success": False, "error": "No result from host"})

            audit_log(
                action_type="filesystem_resize",
                target=device,
                details={"device": device},
                success=result.get("success", False),
                error_message=result.get("error"),
            )

            return jsonify(result)

        except HostCommandTimeout as e:
            audit_log("filesystem_resize", device, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 504
        except HostCommandQueueUnavailable:
            return (
                jsonify({"success": False, "error": "Host command queue not available"}),
                503,
            )
        except Exception as e:
            audit_log(
                "filesystem_resize",
                data.get("device", "unknown"),
                success=False,
                error_message=str(e),
            )
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/lvm")
    @login_required
    @admin_required_decorator
    def api_lvm_info():
        """Get LVM information"""
        try:
            service = get_system_service()
            data = service._get_lvm_info()
            return jsonify({"success": True, "data": data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ============================================================================
    # STORAGE API ENDPOINTS
    # ============================================================================

    @bp.route("/api/storage/drives")
    @login_required
    @admin_required_decorator
    def api_storage_drives():
        """Get storage drives with partition details for treemap visualization"""
        try:
            host_data, age = load_host_monitor_data()
            if not host_data:
                return (
                    jsonify({"success": False, "error": "Host monitor data not available"}),
                    503,
                )

            disks = host_data.get("disks", [])
            lvm = host_data.get("lvm")
            raid = host_data.get("raid")
            docker_mounts = host_data.get("docker_mounts", {})

            # Merge LVM VG free space into disk data
            vg_free = {}
            if lvm and lvm.get("vgs"):
                for vg in lvm["vgs"]:
                    vg_free[vg.get("vg_name")] = {
                        "free_gb": vg.get("vg_free_gb", 0),
                        "size_gb": vg.get("vg_size_gb", 0),
                        "name": vg.get("vg_name"),
                    }

            # Get managed mounts from DB
            managed = {}
            try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT device, label, is_managed FROM storage_mounts WHERE is_managed = TRUE")
                for row in cur.fetchall():
                    managed[row["device"]] = row
                cur.close()
                conn.close()
            except Exception:
                pass

            # Get SMART details and capacity alerts
            smart_details = host_data.get("smart_details", {})
            capacity_alerts = host_data.get("capacity_alerts", [])

            return jsonify(
                {
                    "success": True,
                    "data": {
                        "disks": disks,
                        "lvm": lvm,
                        "raid": raid,
                        "vg_free": vg_free,
                        "managed_mounts": managed,
                        "docker_mounts": docker_mounts,
                        "smart_details": smart_details,
                        "capacity_alerts": capacity_alerts,
                        "data_age": int(age) if age else None,
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

    @bp.route("/api/storage/mounts")
    @login_required
    @admin_required_decorator
    def api_storage_mounts():
        """Get managed storage mounts for Docker bridge"""
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, device, mount_point, filesystem, label, capacity_gb,
                       drive_model, is_managed, created_at, updated_at
                FROM storage_mounts
                ORDER BY device
            """
            )
            mounts = cur.fetchall()

            for m in mounts:
                if m.get("created_at"):
                    m["created_at"] = m["created_at"].isoformat()
                if m.get("updated_at"):
                    m["updated_at"] = m["updated_at"].isoformat()
                if m.get("capacity_gb"):
                    m["capacity_gb"] = float(m["capacity_gb"])

            return jsonify({"success": True, "data": mounts})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/storage/mounts", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_register_storage_mount():
        """Register or update a managed storage mount"""
        try:
            data = request.get_json() or {}
            device = data.get("device")
            if not device:
                return jsonify({"success": False, "error": "Device path required"}), 400

            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO storage_mounts (device, mount_point, filesystem, label, capacity_gb, drive_model, is_managed)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device) DO UPDATE SET
                    mount_point = EXCLUDED.mount_point,
                    filesystem = EXCLUDED.filesystem,
                    label = EXCLUDED.label,
                    capacity_gb = EXCLUDED.capacity_gb,
                    drive_model = EXCLUDED.drive_model,
                    is_managed = EXCLUDED.is_managed,
                    updated_at = NOW()
                RETURNING id, device, mount_point, label
            """,
                (
                    device,
                    data.get("mount_point"),
                    data.get("filesystem"),
                    data.get("label"),
                    data.get("capacity_gb"),
                    data.get("drive_model"),
                    data.get("is_managed", True),
                ),
            )
            result = cur.fetchone()
            conn.commit()

            audit_log(
                "storage_mount_register",
                device,
                details={
                    "mount_point": data.get("mount_point"),
                    "label": data.get("label"),
                },
            )

            return jsonify({"success": True, "data": dict(result)})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/storage/browse")
    @login_required
    @admin_required_decorator
    def api_storage_browse():
        """Browse directory listing for a mount point"""
        try:
            path = request.args.get("path", "")
            if not path:
                return jsonify({"success": False, "error": "Path required"}), 400

            # Security: only allow specific path prefixes
            allowed_prefixes = ("/mnt/", "/home/", "/var/", "/opt/", "/srv/", "/tmp/")
            if not any(path.startswith(p) for p in allowed_prefixes):
                return jsonify({"success": False, "error": "Path not allowed"}), 403

            # Resolve to prevent directory traversal
            real_path = os.path.realpath(path)
            if not any(real_path.startswith(p) for p in allowed_prefixes):
                return jsonify({"success": False, "error": "Path not allowed"}), 403

            if not os.path.isdir(real_path):
                return jsonify({"success": False, "error": "Path is not a directory"}), 400

            entries = []
            try:
                for entry in sorted(os.scandir(real_path), key=lambda e: (not e.is_dir(), e.name.lower())):
                    try:
                        stat = entry.stat()
                        entries.append(
                            {
                                "name": entry.name,
                                "is_dir": entry.is_dir(),
                                "size": stat.st_size if not entry.is_dir() else None,
                                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            }
                        )
                    except (PermissionError, OSError):
                        entries.append(
                            {
                                "name": entry.name,
                                "is_dir": entry.is_dir(),
                                "size": None,
                                "modified": None,
                                "error": "Permission denied",
                            }
                        )
                    if len(entries) >= 200:
                        break
            except PermissionError:
                return jsonify({"success": False, "error": "Permission denied"}), 403

            return jsonify(
                {
                    "success": True,
                    "data": {
                        "path": real_path,
                        "entries": entries,
                        "count": len(entries),
                        "truncated": len(entries) >= 200,
                    },
                }
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/storage/docker-mounts")
    @login_required
    @admin_required_decorator
    def api_storage_docker_mounts():
        """Get Docker stack volume mappings to show which stacks use which paths"""
        try:
            import yaml

            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT name, display_name, compose_directory, color FROM docker_stacks")
            stacks = cur.fetchall()

            # Map of host_path -> [stack info]
            path_to_stacks = {}

            for stack in stacks:
                compose_dir = stack.get("compose_directory", "")
                compose_file = os.path.join(compose_dir, "docker-compose.yml") if compose_dir else ""

                if not compose_file or not os.path.exists(compose_file):
                    continue

                try:
                    with open(compose_file, "r") as f:
                        compose = yaml.safe_load(f)

                    if not compose or "services" not in compose:
                        continue

                    for service_name, service in compose.get("services", {}).items():
                        volumes = service.get("volumes", [])
                        for vol in volumes:
                            if isinstance(vol, str) and ":" in vol:
                                # Format: host_path:container_path or host_path:container_path:mode
                                parts = vol.split(":")
                                host_path = parts[0]
                                container_path = parts[1] if len(parts) > 1 else ""

                                # Skip named volumes (no / at start)
                                if not host_path.startswith("/") and not host_path.startswith("."):
                                    continue

                                # Resolve relative paths
                                if host_path.startswith("."):
                                    host_path = os.path.normpath(os.path.join(compose_dir, host_path))

                                # Get the mount point this path belongs to
                                real_path = os.path.realpath(host_path) if os.path.exists(host_path) else host_path

                                if real_path not in path_to_stacks:
                                    path_to_stacks[real_path] = []

                                path_to_stacks[real_path].append(
                                    {
                                        "stack_name": stack["name"],
                                        "display_name": stack.get("display_name") or stack["name"],
                                        "color": stack.get("color", "#6366f1"),
                                        "service": service_name,
                                        "container_path": container_path,
                                    }
                                )
                            elif isinstance(vol, dict):
                                # Long-form volume definition
                                source = vol.get("source", "")
                                target = vol.get("target", "")
                                vol_type = vol.get("type", "volume")

                                if vol_type == "bind" and source:
                                    if source.startswith("."):
                                        source = os.path.normpath(os.path.join(compose_dir, source))

                                    real_path = os.path.realpath(source) if os.path.exists(source) else source

                                    if real_path not in path_to_stacks:
                                        path_to_stacks[real_path] = []

                                    path_to_stacks[real_path].append(
                                        {
                                            "stack_name": stack["name"],
                                            "display_name": stack.get("display_name") or stack["name"],
                                            "color": stack.get("color", "#6366f1"),
                                            "service": service_name,
                                            "container_path": target,
                                        }
                                    )
                except Exception:
                    # Skip stacks with parse errors
                    continue

            # Group by mount point (find which partition each path belongs to)
            # This will be matched on the frontend with partition mountpoints
            return jsonify(
                {
                    "success": True,
                    "data": {"path_mappings": path_to_stacks, "stack_count": len(stacks)},
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

    @bp.route("/api/storage/preflight", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_storage_preflight():
        """Pre-flight check before storage operations to detect potential issues"""
        try:
            data = request.get_json() or {}
            device = data.get("device", "")
            mountpoint = data.get("mountpoint", "")
            operation = data.get("operation", "")  # extend, shrink, resize, unmount

            warnings = []

            # Check 1: Find containers using the mount path
            if mountpoint:
                try:
                    # Get list of running containers
                    result = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=10)
                    container_ids = result.stdout.strip().split("\n") if result.stdout.strip() else []

                    containers_using_mount = []
                    for cid in container_ids:
                        if not cid:
                            continue
                        # Inspect container mounts
                        inspect = subprocess.run(
                            ["docker", "inspect", "--format", "{{json .Mounts}}", cid],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if inspect.returncode == 0 and mountpoint in inspect.stdout:
                            # Get container name
                            name_result = subprocess.run(
                                ["docker", "inspect", "--format", "{{.Name}}", cid],
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                            container_name = (
                                name_result.stdout.strip().lstrip("/") if name_result.returncode == 0 else cid
                            )
                            containers_using_mount.append(container_name)

                    if containers_using_mount:
                        warnings.append(
                            {
                                "type": "containers",
                                "severity": "info",
                                "message": f"{len(containers_using_mount)} container(s) will be temporarily stopped",
                                "details": containers_using_mount,
                            }
                        )
                except Exception:
                    # Don't fail preflight if container check fails
                    pass

            # Check 2: Stale mount detection (device path doesn't exist but mount is active)
            # NOTE: This check runs inside the Docker container where LVM device symlinks
            # (e.g. /dev/ubuntu-vg/ubuntu-lv) are NOT visible. Skip the os.path.exists()
            # check for LVM paths since the actual operations run on the host via the
            # command queue where devices are accessible.
            is_lvm_device = device and (device.startswith("/dev/mapper/") or len(device.split("/")) >= 4)
            if device and mountpoint and not is_lvm_device:
                try:
                    # Check if device path exists
                    device_exists = os.path.exists(device)

                    # Check if mountpoint is mounted
                    result = subprocess.run(
                        ["findmnt", "-n", "-o", "SOURCE", mountpoint],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    is_mounted = result.returncode == 0 and result.stdout.strip()
                    mounted_device = result.stdout.strip() if is_mounted else None

                    if is_mounted and not device_exists:
                        warnings.append(
                            {
                                "type": "stale_mount",
                                "severity": "high",
                                "message": "Stale mount detected - device path may have changed after rename",
                                "details": f"Mount shows {mounted_device} but {device} does not exist",
                            }
                        )
                    elif is_mounted and mounted_device and mounted_device != device:
                        warnings.append(
                            {
                                "type": "device_mismatch",
                                "severity": "warning",
                                "message": "Mount device differs from expected",
                                "details": f"Expected {device}, found {mounted_device}",
                            }
                        )
                except Exception:
                    pass

            # Check 3: Device busy check
            if device:
                try:
                    # Use lsof to check if device is in use
                    result = subprocess.run(["lsof", device], capture_output=True, text=True, timeout=10)
                    if result.returncode == 0 and result.stdout.strip():
                        lines = result.stdout.strip().split("\n")
                        process_count = len(lines) - 1  # Subtract header
                        if process_count > 0:
                            warnings.append(
                                {
                                    "type": "busy",
                                    "severity": "info",
                                    "message": f"Device has {process_count} open file handle(s)",
                                    "details": "These will be released when containers stop",
                                }
                            )
                except FileNotFoundError:
                    pass  # lsof not installed
                except Exception:
                    pass

            # Check 4: For shrink operations, verify used space
            if operation == "shrink" and mountpoint:
                try:
                    result = subprocess.run(
                        ["df", "-BG", "--output=used", mountpoint],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        lines = result.stdout.strip().split("\n")
                        if len(lines) > 1:
                            used_str = lines[1].strip().rstrip("G")
                            try:
                                used_gb = int(used_str)
                                if used_gb > 0:
                                    warnings.append(
                                        {
                                            "type": "shrink_info",
                                            "severity": "info",
                                            "message": f"Minimum size: {used_gb} GB (current usage)",
                                            "details": "Cannot shrink below used space",
                                        }
                                    )
                            except ValueError:
                                pass
                except Exception:
                    pass

            # Determine if operation can proceed
            high_severity = [w for w in warnings if w.get("severity") == "high"]
            can_proceed = len(high_severity) == 0

            return jsonify(
                {
                    "success": True,
                    "warnings": warnings,
                    "can_proceed": can_proceed,
                    "warning_count": len(warnings),
                }
            )

        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
