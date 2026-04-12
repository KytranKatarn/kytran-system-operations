"""
System Operations — Network Routes
====================================
Extracted from routes.py during ADR-045 route split refactor.

Endpoints: api_network, api_network_port_map, api_network_bandwidth, api_port_usage
"""

from flask import jsonify, request
from flask_login import login_required
from psycopg2.extras import RealDictCursor
import os
import json as json_lib

from .helpers import (
    BASE_DIR,
    load_host_monitor_data,
    get_db,
    parse_compose_host_port,
    find_compose_file,
)
from .system_service import get_system_service


def register_network_routes(bp, admin_required_decorator):
    """Register network-related routes on the given blueprint."""

    @bp.route("/api/network")
    @login_required
    @admin_required_decorator
    def api_network():
        """Get network information - merges host monitor data with container network"""
        try:
            service = get_system_service()
            data = service.get_network_info()

            # Merge host monitor network data
            host_data, host_age = load_host_monitor_data()
            if host_data:
                host_net = host_data.get("network") or {}
                data["host_data_age"] = int(host_age) if host_age else None

                # Override hostname with real host hostname
                if host_net.get("hostname"):
                    data["host_hostname"] = host_net["hostname"]

                # Host IP and gateway from host monitor
                if host_net.get("primary_ip"):
                    data["host_ip"] = host_net["primary_ip"]
                if host_net.get("gateway"):
                    data["gateway"] = host_net["gateway"]
                if host_net.get("dns_servers"):
                    data["host_dns_servers"] = host_net["dns_servers"]

                # Real host interfaces replace the guessed ones
                if host_net.get("interfaces"):
                    data["host_interfaces"] = [
                        {**iface, "type": "host", "note": "Host interface"} for iface in host_net["interfaces"]
                    ]

                # Host listening ports and connections
                if host_net.get("listening_ports"):
                    data["host_listening_ports"] = host_net["listening_ports"]
                if host_net.get("connections"):
                    data["host_connections"] = host_net["connections"]

            # Add port-to-stack context (inline, lightweight)
            try:
                import yaml

                conn = get_db()
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("SELECT name, compose_directory, color, web_ui_ports FROM docker_stacks ORDER BY name")
                stack_rows = cur.fetchall()
                cur.close()
                conn.close()

                port_to_stack = {}
                stacks_summary = []
                for sr in stack_rows:
                    compose_dir = sr.get("compose_directory") or ""
                    stack_ports = []
                    compose_file = find_compose_file(compose_dir)
                    if compose_file:
                        try:
                            with open(compose_file, "r") as f:
                                parsed = yaml.safe_load(f) or {}
                            for svc_name, svc_config in (parsed.get("services") or {}).items():
                                for port_entry in svc_config.get("ports") or []:
                                    host_port = parse_compose_host_port(port_entry)
                                    if host_port is None:
                                        continue
                                    port_to_stack[str(host_port)] = sr["name"]
                                    stack_ports.append(host_port)
                        except (yaml.YAMLError, OSError):
                            pass
                    stacks_summary.append(
                        {
                            "name": sr["name"],
                            "color": sr.get("color") or "#888888",
                            "ports": stack_ports,
                            "web_ui_ports": sr.get("web_ui_ports") or [],
                        }
                    )
                data["port_to_stack"] = port_to_stack
                data["stacks_summary"] = stacks_summary
            except Exception:
                data["port_to_stack"] = {}
                data["stacks_summary"] = []

            return jsonify({"success": True, "data": data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    @bp.route("/api/network/port-map")
    @login_required
    @admin_required_decorator
    def api_network_port_map():
        """Get port-to-stack mapping by cross-referencing compose files with listening ports"""
        try:
            import yaml

            conn = get_db()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "SELECT id, name, compose_directory, color, is_system, web_ui_ports FROM docker_stacks ORDER BY name"
            )
            stacks_rows = cur.fetchall()

            # Get host listening ports
            host_data, _ = load_host_monitor_data()
            host_ports_list = []
            if host_data:
                host_ports_list = (host_data.get("network") or {}).get("listening_ports", [])
            listening_port_set = {p["port"] for p in host_ports_list}

            port_to_stack = {}
            stacks = []

            for stack_row in stacks_rows:
                stack_name = stack_row["name"]
                compose_dir = stack_row.get("compose_directory") or ""
                color = stack_row.get("color") or "#888888"
                web_ui_ports = stack_row.get("web_ui_ports") or []
                stack_ports = []

                # Parse compose file for port mappings
                compose_file = find_compose_file(compose_dir)

                if compose_file:
                    try:
                        with open(compose_file, "r") as f:
                            parsed = yaml.safe_load(f) or {}
                        for svc_name, svc_config in (parsed.get("services") or {}).items():
                            for port_entry in svc_config.get("ports") or []:
                                host_port = parse_compose_host_port(port_entry)
                                if host_port is None:
                                    continue

                                is_open = host_port in listening_port_set
                                # Find process info from host ports
                                process_info = None
                                for hp in host_ports_list:
                                    if hp["port"] == host_port:
                                        process_info = hp.get("process")
                                        break

                                stack_ports.append(
                                    {
                                        "port": host_port,
                                        "service": svc_name,
                                        "open": is_open,
                                        "process": process_info,
                                    }
                                )
                                port_to_stack[str(host_port)] = stack_name
                    except (yaml.YAMLError, OSError):
                        pass

                stacks.append(
                    {
                        "name": stack_name,
                        "color": color,
                        "is_system": stack_row.get("is_system", False),
                        "ports": stack_ports,
                        "web_ui_ports": web_ui_ports,
                    }
                )

            # Build unassigned ports list
            assigned_ports = {p["port"] for s in stacks for p in s["ports"]}
            unassigned = [
                {
                    "port": p["port"],
                    "process": p.get("process"),
                    "ip": p.get("ip", "0.0.0.0"),
                }
                for p in host_ports_list
                if p["port"] not in assigned_ports
            ]

            return jsonify(
                {
                    "success": True,
                    "stacks": stacks,
                    "unassigned_ports": unassigned,
                    "port_to_stack": port_to_stack,
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

    @bp.route("/api/network/bandwidth")
    @login_required
    @admin_required_decorator
    def api_network_bandwidth():
        """Get bandwidth data: historical samples + current interface/container stats"""
        try:
            bandwidth_file = os.path.join(BASE_DIR, "bandwidth_history.json")
            samples = []
            if os.path.exists(bandwidth_file):
                try:
                    with open(bandwidth_file, "r") as f:
                        history = json_lib.load(f)
                    samples = history.get("samples", [])
                except (json_lib.JSONDecodeError, OSError):
                    pass

            host_data, host_age = load_host_monitor_data()
            interfaces = []
            containers = []
            if host_data:
                net = host_data.get("network") or {}
                interfaces = net.get("bandwidth_interfaces", [])
                containers = host_data.get("container_bandwidth", [])

            return jsonify(
                {
                    "success": True,
                    "samples": samples,
                    "current": {
                        "interfaces": interfaces,
                        "containers": containers,
                        "data_age": int(host_age) if host_age else None,
                    },
                }
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/network/port-usage")
    @login_required
    @admin_required_decorator
    def api_port_usage():
        """Get comprehensive port usage for conflict detection"""
        try:
            host_data, host_age = load_host_monitor_data()
            if not host_data:
                return (
                    jsonify({"success": False, "error": "Host monitor data not available"}),
                    503,
                )

            port_usage = host_data.get("port_usage", {})

            # Known ports for planned services (for highlighting)
            planned_ports = {
                "home_assistant": [8123, 1900, 5353],
                "plex": [32400, 32469, 1900, 3005, 8324, 32410, 32411, 32412, 32413, 32414],
                "adguard": [53, 80, 443, 3000, 853, 784, 8853],
                "ntopng": [3000, 5556],
            }

            # Check for conflicts with planned services
            conflicts = []
            for service, ports in planned_ports.items():
                for port in ports:
                    if str(port) in port_usage or port in port_usage:
                        port_info = port_usage.get(str(port)) or port_usage.get(port, {})
                        conflicts.append(
                            {
                                "port": port,
                                "planned_service": service,
                                "current_usage": port_info,
                            }
                        )

            return jsonify(
                {
                    "success": True,
                    "ports": port_usage,
                    "planned_conflicts": conflicts,
                    "data_age": int(host_age) if host_age else None,
                }
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
