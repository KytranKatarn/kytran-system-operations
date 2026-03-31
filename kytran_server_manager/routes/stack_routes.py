"""Stack Management Routes — Docker Compose stack CRUD and actions"""

import os
import json as json_lib
import sqlite3
from datetime import datetime

from flask import jsonify, request, Response
from flask_login import login_required


from ..helpers import (
    BASE_DIR,
    get_db,
    audit_log,
    require_reauth,
    find_compose_file,
    parse_compose_host_port,
)


def register_stack_routes(bp, admin_required_decorator):
    @bp.route("/api/stack/compose", methods=["GET"])
    @login_required
    @admin_required_decorator
    def api_get_compose():
        """Get docker-compose.yml content and parse services"""
        try:
            import yaml

            compose_file = os.path.join(BASE_DIR, "docker-compose.yml")

            if not os.path.exists(compose_file):
                return jsonify({"success": False, "error": "Compose file not found"}), 404

            # Read file content
            with open(compose_file, "r") as f:
                content = f.read()

            # Get file modification time
            stat = os.stat(compose_file)
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            # Parse services
            services = []
            try:
                parsed = yaml.safe_load(content)
                if parsed and "services" in parsed:
                    # Get running containers to check status
                    import subprocess

                    result = subprocess.run(
                        ["docker", "ps", "--format", "{{.Names}}"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    running_containers = set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()

                    for name, config in parsed["services"].items():
                        services.append(
                            {
                                "name": name,
                                "image": config.get("image", ""),
                                "running": name in running_containers
                                or f"archie_brain-{name}-1" in running_containers
                                or f"archie_brain_{name}_1" in running_containers,
                            }
                        )
            except yaml.YAMLError:
                # YAML parse error - still return content but note the error
                pass

            return jsonify(
                {
                    "success": True,
                    "content": content,
                    "modified": modified,
                    "services": services,
                    "file": compose_file,
                }
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/stack/compose", methods=["PUT"])
    @login_required
    @admin_required_decorator
    def api_save_compose():
        """Save docker-compose.yml content"""
        try:
            import yaml

            data = request.get_json() or {}
            content = data.get("content", "")

            if not content:
                return jsonify({"success": False, "error": "No content provided"}), 400

            compose_file = data.get("file", os.path.join(BASE_DIR, "docker-compose.yml"))

            # Security: Only allow specific files to be edited
            allowed_files = [
                os.path.join(BASE_DIR, "docker-compose.yml"),
                os.path.join(BASE_DIR, ".env"),
                os.path.join(BASE_DIR, "platform_v2", ".env"),
            ]

            if compose_file not in allowed_files:
                return (
                    jsonify({"success": False, "error": "File not allowed to be edited"}),
                    403,
                )

            # Validate YAML syntax
            try:
                yaml.safe_load(content)
            except yaml.YAMLError as ye:
                return (
                    jsonify({"success": False, "error": f"Invalid YAML syntax: {str(ye)}"}),
                    400,
                )

            # Create backup
            backup_file = f"{compose_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if os.path.exists(compose_file):
                import shutil

                shutil.copy2(compose_file, backup_file)

            # Write new content
            with open(compose_file, "w") as f:
                f.write(content)

            # Audit log
            audit_log(
                action_type="compose_file_edit",
                target=compose_file,
                details={"backup_file": backup_file, "content_length": len(content)},
                success=True,
            )

            return jsonify({"success": True, "message": "Compose file saved", "backup": backup_file})
        except Exception as e:
            audit_log("compose_file_edit", compose_file, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/stack/env", methods=["GET"])
    @login_required
    @admin_required_decorator
    def api_get_env():
        """Get environment file content"""
        try:
            filename = request.args.get("file", ".env")

            # Security: Only allow specific env files
            allowed_files = {
                ".env": os.path.join(BASE_DIR, ".env"),
                "platform_v2/.env": os.path.join(BASE_DIR, "platform_v2", ".env"),
            }

            if filename not in allowed_files:
                return jsonify({"success": False, "error": "File not allowed"}), 403

            env_file = allowed_files[filename]

            if not os.path.exists(env_file):
                return jsonify({"success": False, "error": "File not found"}), 404

            with open(env_file, "r") as f:
                content = f.read()

            stat = os.stat(env_file)
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            return jsonify(
                {
                    "success": True,
                    "content": content,
                    "modified": modified,
                    "file": env_file,
                }
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/stack/env", methods=["PUT"])
    @login_required
    @admin_required_decorator
    def api_save_env():
        """Save environment file content"""
        try:
            data = request.get_json() or {}
            content = data.get("content", "")
            filename = data.get("file", "")

            # Security: Only allow specific env files
            allowed_files = [
                os.path.join(BASE_DIR, ".env"),
                os.path.join(BASE_DIR, "platform_v2", ".env"),
            ]

            if filename not in allowed_files:
                return (
                    jsonify({"success": False, "error": "File not allowed to be edited"}),
                    403,
                )

            # Create backup
            backup_file = f"{filename}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if os.path.exists(filename):
                import shutil

                shutil.copy2(filename, backup_file)

            # Write new content
            with open(filename, "w") as f:
                f.write(content)

            # Audit log
            audit_log(
                action_type="env_file_edit",
                target=filename,
                details={"backup_file": backup_file},
                success=True,
            )

            return jsonify(
                {
                    "success": True,
                    "message": "Environment file saved",
                    "backup": backup_file,
                }
            )
        except Exception as e:
            audit_log("env_file_edit", filename, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500

    # ============================================================================
    # MULTI-STACK MANAGEMENT ENDPOINTS
    # ============================================================================

    @bp.route("/api/stacks")
    @login_required
    @admin_required_decorator
    def api_list_stacks():
        """List all Docker stacks with live container status"""
        try:
            import subprocess

            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, display_name, description, color, compose_directory,
                       is_system, created_at, updated_at
                FROM docker_stacks
                ORDER BY is_system DESC, display_name ASC
            """
            )
            stacks = cur.fetchall()

            results = []
            for stack in stacks:
                stack_data = dict(stack)
                compose_dir = stack_data.get("compose_directory", "")
                compose_file = os.path.join(compose_dir, "docker-compose.yml") if compose_dir else ""

                # Convert datetime fields
                if stack_data.get("created_at"):
                    stack_data["created_at"] = stack_data["created_at"].isoformat()
                if stack_data.get("updated_at"):
                    stack_data["updated_at"] = stack_data["updated_at"].isoformat()

                # Get live container status
                containers_running = 0
                containers_total = 0
                status = "unknown"
                container_list = []

                if compose_dir and compose_file and os.path.exists(compose_file):
                    try:
                        result = subprocess.run(
                            [
                                "docker",
                                "compose",
                                "-f",
                                compose_file,
                                "--project-directory",
                                compose_dir,
                                "ps",
                                "--format",
                                "json",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            containers = []
                            for line in result.stdout.strip().splitlines():
                                line = line.strip()
                                if line:
                                    try:
                                        containers.append(json_lib.loads(line))
                                    except json_lib.JSONDecodeError:
                                        pass
                            containers_total = len(containers)
                            containers_running = sum(1 for c in containers if c.get("State", "").lower() == "running")
                            if containers_total > 0 and containers_running == containers_total:
                                status = "running"
                            elif containers_running > 0:
                                status = "partial"
                            else:
                                status = "stopped"

                            # Build container list for display
                            for c in containers:
                                container_list.append(
                                    {
                                        "name": c.get("Service") or c.get("Name", "unknown"),
                                        "state": c.get("State", "unknown").lower(),
                                        "status": c.get("Status", ""),
                                    }
                                )
                        else:
                            status = "stopped"
                    except (subprocess.TimeoutExpired, Exception):
                        status = "unknown"

                stack_data["status"] = status
                stack_data["containers_running"] = containers_running
                stack_data["containers_total"] = containers_total
                stack_data["containers"] = container_list
                results.append(stack_data)

            return jsonify({"success": True, "data": results})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/stacks", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_create_stack():
        """Create a new Docker stack"""
        conn = None
        cur = None
        stack_dir = None
        try:
            import re
            import yaml

            data = request.get_json() or {}
            name = data.get("name", "").strip()
            display_name = data.get("display_name", "").strip()
            description = data.get("description", "").strip()
            color = data.get("color", "#6366f1").strip()
            compose_content = data.get("compose_content", "").strip()
            env_content = data.get("env_content", "").strip()

            # Validate name
            if not name:
                return jsonify({"success": False, "error": "Stack name is required"}), 400

            if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", name):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Invalid name. Use lowercase letters, numbers, "
                            "and hyphens. Must start/end with alphanumeric.",
                        }
                    ),
                    400,
                )

            if not display_name:
                display_name = name.replace("-", " ").title()

            # Stack directory
            stack_dir = f"/mnt/stacks/{name}"
            if os.path.exists(stack_dir):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"Stack directory already exists: {stack_dir}",
                        }
                    ),
                    409,
                )

            os.makedirs(stack_dir, exist_ok=False)

            # Write compose file if provided
            if compose_content:
                try:
                    yaml.safe_load(compose_content)
                except yaml.YAMLError as ye:
                    # Clean up directory on validation failure
                    import shutil

                    shutil.rmtree(stack_dir, ignore_errors=True)
                    stack_dir = None
                    return (
                        jsonify({"success": False, "error": f"Invalid YAML: {str(ye)}"}),
                        400,
                    )
                with open(os.path.join(stack_dir, "docker-compose.yml"), "w") as f:
                    f.write(compose_content)

            # Write env file if provided
            if env_content:
                with open(os.path.join(stack_dir, ".env"), "w") as f:
                    f.write(env_content)

            # Insert into database
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO docker_stacks (name, display_name, description, color, compose_directory, is_system)
                VALUES (?, ?, ?, ?, ?, FALSE), name, display_name, description, color, compose_directory, is_system, created_at, updated_at
            """,
                (name, display_name, description, color, stack_dir),
            )
            stack = dict(cur.fetchone())
            conn.commit()
            conn = None
            cur = None

            # Convert datetime fields
            if stack.get("created_at"):
                stack["created_at"] = stack["created_at"].isoformat()
            if stack.get("updated_at"):
                stack["updated_at"] = stack["updated_at"].isoformat()

            audit_log(
                "stack_create",
                name,
                details={"display_name": display_name, "directory": stack_dir},
            )

            return jsonify({"success": True, "data": stack}), 201

        except sqlite3.IntegrityError:
            if conn:
                conn.rollback()
            if cur:
                cur.close()
            if conn:
                conn.close()
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"A stack with name '{name}' already exists",
                    }
                ),
                409,
            )
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if cur:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            # Clean up directory on error if we created it
            if stack_dir and os.path.exists(stack_dir):
                import shutil

                shutil.rmtree(stack_dir, ignore_errors=True)
            audit_log(
                "stack_create",
                data.get("name", "unknown"),
                success=False,
                error_message=str(e),
            )
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/stacks/<stack_name>")
    @login_required
    @admin_required_decorator
    def api_get_stack(stack_name):
        """Get detailed information for a single Docker stack"""
        try:
            import subprocess

            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, display_name, description, color, compose_directory,
                       is_system, created_at, updated_at
                FROM docker_stacks
                WHERE name = ?
            """,
                (stack_name,),
            )
            stack = cur.fetchone()

            if not stack:
                return jsonify({"success": False, "error": "Stack not found"}), 404

            stack_data = dict(stack)
            compose_dir = stack_data.get("compose_directory", "")
            compose_file = os.path.join(compose_dir, "docker-compose.yml") if compose_dir else ""

            # Convert datetime fields
            if stack_data.get("created_at"):
                stack_data["created_at"] = stack_data["created_at"].isoformat()
            if stack_data.get("updated_at"):
                stack_data["updated_at"] = stack_data["updated_at"].isoformat()

            # Get container details
            containers = []
            containers_running = 0
            containers_total = 0

            if compose_dir and compose_file and os.path.exists(compose_file):
                try:
                    result = subprocess.run(
                        [
                            "docker",
                            "compose",
                            "-f",
                            compose_file,
                            "--project-directory",
                            compose_dir,
                            "ps",
                            "--format",
                            "json",
                            "-a",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        for line in result.stdout.strip().splitlines():
                            line = line.strip()
                            if line:
                                try:
                                    containers.append(json_lib.loads(line))
                                except json_lib.JSONDecodeError:
                                    pass
                        containers_total = len(containers)
                        containers_running = sum(1 for c in containers if c.get("State", "").lower() == "running")
                except (subprocess.TimeoutExpired, Exception):
                    pass

            stack_data["containers"] = containers
            stack_data["containers_running"] = containers_running
            stack_data["containers_total"] = containers_total

            return jsonify({"success": True, "data": stack_data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/stacks/<stack_name>", methods=["DELETE"])
    @login_required
    @admin_required_decorator
    def api_delete_stack(stack_name):
        """Delete a Docker stack"""
        try:
            import subprocess

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

            remove_files = data.get("remove_files", False)

            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, display_name, compose_directory, is_system
                FROM docker_stacks
                WHERE name = ?
            """,
                (stack_name,),
            )
            stack = cur.fetchone()

            if not stack:
                cur.close()
                conn.close()
                return jsonify({"success": False, "error": "Stack not found"}), 404

            if stack["is_system"]:
                cur.close()
                conn.close()
                return (
                    jsonify({"success": False, "error": "Cannot delete system stack"}),
                    403,
                )

            compose_dir = stack.get("compose_directory", "")
            compose_file = os.path.join(compose_dir, "docker-compose.yml") if compose_dir else ""

            # Stop containers first
            if compose_dir and compose_file and os.path.exists(compose_file):
                try:
                    subprocess.run(
                        [
                            "docker",
                            "compose",
                            "-f",
                            compose_file,
                            "--project-directory",
                            compose_dir,
                            "down",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                except (subprocess.TimeoutExpired, Exception):
                    pass  # Best effort stop

            # Delete from database
            cur.execute("DELETE FROM docker_stacks WHERE name = ?", (stack_name,))
            conn.commit()

            # Optionally remove files
            files_removed = False
            if remove_files and compose_dir and compose_dir.startswith("/mnt/stacks/"):
                import shutil

                if os.path.exists(compose_dir):
                    shutil.rmtree(compose_dir)
                    files_removed = True

            audit_log(
                "stack_delete",
                stack_name,
                details={
                    "display_name": stack["display_name"],
                    "directory": compose_dir,
                    "files_removed": files_removed,
                },
            )

            return jsonify(
                {
                    "success": True,
                    "message": f"Stack '{stack_name}' deleted",
                    "files_removed": files_removed,
                }
            )
        except Exception as e:
            audit_log("stack_delete", stack_name, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    # ============================================================================
    # PER-STACK ACTION, COMPOSE, ENV, AND LOGS ENDPOINTS
    # ============================================================================

    @bp.route("/api/stacks/<stack_name>/action", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_stack_action(stack_name):
        """Execute a docker compose action on a stack (up/down/restart/pull)"""
        try:
            import subprocess

            data = request.get_json() or {}
            action = data.get("action", "")
            confirm = data.get("confirm", False)

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

            # Look up stack from DB
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, display_name, compose_directory, is_system
                FROM docker_stacks
                WHERE name = ?
            """,
                (stack_name,),
            )
            stack = cur.fetchone()

            if not stack:
                return jsonify({"success": False, "error": "Stack not found"}), 404

            # System stack + destructive action requires re-auth
            destructive_actions = ["down", "restart", "pull"]
            if stack["is_system"] and action in destructive_actions:
                reauth = require_reauth()
                if reauth is not None:
                    return reauth

            compose_dir = stack["compose_directory"]
            compose_file = os.path.join(compose_dir, "docker-compose.yml")

            if not os.path.exists(compose_file):
                return (
                    jsonify({"success": False, "error": "Compose file not found for this stack"}),
                    404,
                )

            # Build command
            cmd = [
                "docker",
                "compose",
                "-f",
                compose_file,
                "--project-directory",
                compose_dir,
                action,
            ]
            if action == "up":
                cmd.append("-d")

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            except subprocess.TimeoutExpired:
                audit_log(
                    "stack_action",
                    stack_name,
                    details={"action": action},
                    success=False,
                    error_message="Command timed out after 120 seconds",
                )
                return (
                    jsonify({"success": False, "error": "Command timed out after 120 seconds"}),
                    500,
                )

            success = result.returncode == 0
            audit_log(
                action_type="stack_action",
                target=stack_name,
                details={
                    "action": action,
                    "stdout": result.stdout[:500] if result.stdout else "",
                    "stderr": result.stderr[:500] if result.stderr else "",
                },
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
        except Exception as e:
            audit_log(
                "stack_action",
                stack_name,
                details={"action": data.get("action")},
                success=False,
                error_message=str(e),
            )
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/stacks/<stack_name>/compose")
    @login_required
    @admin_required_decorator
    def api_get_stack_compose(stack_name):
        """Get docker-compose.yml content and service status for a stack"""
        try:
            import yaml
            import subprocess

            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, compose_directory, is_system
                FROM docker_stacks
                WHERE name = ?
            """,
                (stack_name,),
            )
            stack = cur.fetchone()

            if not stack:
                return jsonify({"success": False, "error": "Stack not found"}), 404

            compose_dir = stack["compose_directory"]
            compose_file = os.path.join(compose_dir, "docker-compose.yml")

            if not os.path.exists(compose_file):
                return jsonify({"success": False, "error": "Compose file not found"}), 404

            # Read file content
            with open(compose_file, "r") as f:
                content = f.read()

            # Get modification time
            stat = os.stat(compose_file)
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            # Parse YAML and extract services
            services = []
            try:
                parsed = yaml.safe_load(content)
                if parsed and "services" in parsed:
                    # Get running containers to check status
                    result = subprocess.run(
                        ["docker", "ps", "--format", "{{.Names}}"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    running_containers = set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()

                    for svc_name, svc_config in parsed["services"].items():
                        # Check multiple name patterns
                        name_patterns = [
                            svc_name,
                            f"{stack_name}-{svc_name}-1",
                            f"{stack_name}_{svc_name}_1",
                        ]
                        # For system stack, also check archie_brain patterns
                        if stack["is_system"]:
                            name_patterns.append(f"archie_brain-{svc_name}-1")
                            name_patterns.append(f"archie_brain_{svc_name}_1")

                        is_running = any(p in running_containers for p in name_patterns)

                        # Extract ports
                        ports = svc_config.get("ports", [])
                        port_strings = [str(p) for p in ports] if ports else []

                        services.append(
                            {
                                "name": svc_name,
                                "image": svc_config.get("image", ""),
                                "ports": port_strings,
                                "running": is_running,
                            }
                        )
            except yaml.YAMLError:
                pass  # Still return content even if YAML is invalid

            return jsonify(
                {
                    "success": True,
                    "content": content,
                    "modified": modified,
                    "services": services,
                    "file": compose_file,
                    "is_system": stack["is_system"],
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

    @bp.route("/api/stacks/<stack_name>/compose", methods=["PUT"])
    @login_required
    @admin_required_decorator
    def api_save_stack_compose(stack_name):
        """Save docker-compose.yml content for a stack"""
        try:
            import yaml

            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, compose_directory, is_system
                FROM docker_stacks
                WHERE name = ?
            """,
                (stack_name,),
            )
            stack = cur.fetchone()

            if not stack:
                return jsonify({"success": False, "error": "Stack not found"}), 404

            # System stack requires re-auth
            if stack["is_system"]:
                reauth = require_reauth()
                if reauth is not None:
                    return reauth

            data = request.get_json() or {}
            content = data.get("content", "")

            if not content:
                return jsonify({"success": False, "error": "No content provided"}), 400

            # Validate YAML syntax
            try:
                yaml.safe_load(content)
            except yaml.YAMLError as ye:
                return (
                    jsonify({"success": False, "error": f"Invalid YAML syntax: {str(ye)}"}),
                    400,
                )

            compose_dir = stack["compose_directory"]
            compose_file = os.path.join(compose_dir, "docker-compose.yml")

            # Security: verify resolved path is within stack directory
            real_compose = os.path.realpath(compose_file)
            real_compose_dir = os.path.realpath(compose_dir)
            if not real_compose.startswith(real_compose_dir + os.sep) and real_compose != real_compose_dir:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Invalid file path - directory traversal not allowed",
                        }
                    ),
                    403,
                )

            # Create backup with timestamp
            backup_file = f"{real_compose}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if os.path.exists(real_compose):
                import shutil

                shutil.copy2(real_compose, backup_file)

            # Write new content
            with open(real_compose, "w") as f:
                f.write(content)

            audit_log(
                action_type="stack_compose_edit",
                target=stack_name,
                details={
                    "backup_file": backup_file,
                    "file": compose_file,
                    "content_length": len(content),
                },
                success=True,
            )

            return jsonify({"success": True, "message": "Compose file saved", "backup": backup_file})
        except Exception as e:
            audit_log("stack_compose_edit", stack_name, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/stacks/<stack_name>/env")
    @login_required
    @admin_required_decorator
    def api_get_stack_env(stack_name):
        """Get environment files for a stack"""
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, compose_directory, is_system
                FROM docker_stacks
                WHERE name = ?
            """,
                (stack_name,),
            )
            stack = cur.fetchone()

            if not stack:
                return jsonify({"success": False, "error": "Stack not found"}), 404

            compose_dir = stack["compose_directory"]
            env_files = []

            # Read .env from stack directory
            env_path = os.path.join(compose_dir, ".env")
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    env_content = f.read()
                stat = os.stat(env_path)
                modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                env_files.append(
                    {
                        "filename": ".env",
                        "path": env_path,
                        "content": env_content,
                        "modified": modified,
                    }
                )

            # For system stack, also read platform_v2/.env
            if stack["is_system"]:
                platform_env_path = os.path.join(compose_dir, "platform_v2", ".env")
                if os.path.exists(platform_env_path):
                    with open(platform_env_path, "r") as f:
                        platform_env_content = f.read()
                    stat = os.stat(platform_env_path)
                    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    env_files.append(
                        {
                            "filename": "platform_v2/.env",
                            "path": platform_env_path,
                            "content": platform_env_content,
                            "modified": modified,
                        }
                    )

            return jsonify(
                {
                    "success": True,
                    "data": env_files,
                    "is_system": stack["is_system"],
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

    @bp.route("/api/stacks/<stack_name>/env", methods=["PUT"])
    @login_required
    @admin_required_decorator
    def api_save_stack_env(stack_name):
        """Save an environment file for a stack"""
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, compose_directory, is_system
                FROM docker_stacks
                WHERE name = ?
            """,
                (stack_name,),
            )
            stack = cur.fetchone()

            if not stack:
                return jsonify({"success": False, "error": "Stack not found"}), 404

            # System stack requires re-auth
            if stack["is_system"]:
                reauth = require_reauth()
                if reauth is not None:
                    return reauth

            data = request.get_json() or {}
            content = data.get("content", "")
            filename = data.get("filename", ".env")

            if not content:
                return jsonify({"success": False, "error": "No content provided"}), 400

            compose_dir = stack["compose_directory"]

            # Security: verify resolved path is within stack directory
            target_path = os.path.join(compose_dir, filename)
            real_target = os.path.realpath(target_path)
            real_compose_dir = os.path.realpath(compose_dir)

            if not real_target.startswith(real_compose_dir + os.sep) and real_target != real_compose_dir:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Invalid file path - directory traversal not allowed",
                        }
                    ),
                    403,
                )

            # Create backup with timestamp
            backup_file = f"{real_target}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if os.path.exists(real_target):
                import shutil

                shutil.copy2(real_target, backup_file)

            # Write new content
            with open(real_target, "w") as f:
                f.write(content)

            audit_log(
                action_type="stack_env_edit",
                target=stack_name,
                details={
                    "filename": filename,
                    "file": real_target,
                    "backup_file": backup_file,
                },
                success=True,
            )

            return jsonify(
                {
                    "success": True,
                    "message": "Environment file saved",
                    "backup": backup_file,
                }
            )
        except Exception as e:
            audit_log("stack_env_edit", stack_name, success=False, error_message=str(e))
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/stacks/<stack_name>/logs")
    @login_required
    @admin_required_decorator
    def api_stack_logs(stack_name):
        """Get docker compose logs for a stack"""
        try:
            import subprocess

            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, compose_directory, is_system
                FROM docker_stacks
                WHERE name = ?
            """,
                (stack_name,),
            )
            stack = cur.fetchone()

            if not stack:
                return jsonify({"success": False, "error": "Stack not found"}), 404

            compose_dir = stack["compose_directory"]
            compose_file = os.path.join(compose_dir, "docker-compose.yml")

            if not os.path.exists(compose_file):
                return jsonify({"success": False, "error": "Compose file not found"}), 404

            tail = request.args.get("tail", "100")
            service = request.args.get("service", "")

            cmd = [
                "docker",
                "compose",
                "-f",
                compose_file,
                "--project-directory",
                compose_dir,
                "logs",
                "--tail",
                tail,
                "--no-color",
            ]
            if service:
                cmd.append(service)

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            logs = result.stdout + result.stderr
            return Response(logs, mimetype="text/plain")
        except Exception as e:
            return Response(f"Error fetching logs: {str(e)}", status=500, mimetype="text/plain")
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    # ============================================================================
    # SHARED STACK STATUS API (for cross-module use)
    # ============================================================================

    @bp.route("/api/stacks/status")
    @login_required
    def api_stacks_status():
        """
        Lightweight stack status endpoint for cross-module use.
        Returns essential status info without requiring admin privileges.
        Used by Security & Network module to show stack status.

        Query params:
            names: comma-separated list of stack names to filter (optional)
        """
        try:
            import subprocess

            conn = get_db()
            cur = conn.cursor()

            # Get optional filter
            names_filter = request.args.get("names", "")
            filter_names = [n.strip() for n in names_filter.split(",") if n.strip()] if names_filter else None

            if filter_names:
                cur.execute(
                    """
                    SELECT id, name, display_name, color, compose_directory
                    FROM docker_stacks
                    WHERE name = (?)
                    ORDER BY display_name ASC
                    """,
                    (filter_names,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, name, display_name, color, compose_directory
                    FROM docker_stacks
                    ORDER BY is_system DESC, display_name ASC
                    """
                )
            stacks = cur.fetchall()

            results = []
            for stack in stacks:
                compose_dir = stack.get("compose_directory", "")
                compose_file = find_compose_file(compose_dir) if compose_dir else None

                # Get live container status
                containers_running = 0
                containers_total = 0
                status = "unknown"

                if compose_file and os.path.exists(compose_file):
                    try:
                        result = subprocess.run(
                            [
                                "docker",
                                "compose",
                                "-f",
                                compose_file,
                                "--project-directory",
                                compose_dir,
                                "ps",
                                "--format",
                                "json",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            containers = []
                            for line in result.stdout.strip().splitlines():
                                line = line.strip()
                                if line:
                                    try:
                                        containers.append(json_lib.loads(line))
                                    except json_lib.JSONDecodeError:
                                        pass
                            containers_total = len(containers)
                            containers_running = sum(1 for c in containers if c.get("State", "").lower() == "running")
                            if containers_total > 0 and containers_running == containers_total:
                                status = "running"
                            elif containers_running > 0:
                                status = "partial"
                            else:
                                status = "stopped"
                        else:
                            status = "stopped"
                    except (subprocess.TimeoutExpired, Exception):
                        status = "unknown"

                results.append(
                    {
                        "name": stack["name"],
                        "display_name": stack["display_name"],
                        "color": stack["color"],
                        "status": status,
                        "containers_running": containers_running,
                        "containers_total": containers_total,
                    }
                )

            return jsonify({"success": True, "stacks": results})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass
