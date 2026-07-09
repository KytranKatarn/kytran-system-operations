"""ARCHIE Chat proxy — forwards overlay API calls to the platform hub."""

import json
import logging
import os

import requests
from flask import Response, current_app, request
from flask_login import login_required

logger = logging.getLogger(__name__)
_PROXY_TIMEOUT = 120
_HOST_MONITOR_PATH = "/data/host_monitor_data.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hub_headers() -> dict:
    api_key = current_app.config.get("KSO_CHAT_API_KEY", "")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": request.content_type or "application/json",
        "Accept": "application/json",
    }


def _hub_base() -> str:
    url = current_app.config.get("ARCHIE_HUB_URL") or "http://192.168.1.200:3000"
    return url.rstrip("/")


# ---------------------------------------------------------------------------
# KSO Live Briefing
# ---------------------------------------------------------------------------


def _build_kso_briefing() -> str:
    """Build a live system briefing from host_monitor_data.json for B.A.S.E."""
    from services.time_utils import now_local_str  # noqa: imported from KSO services

    try:
        now_str = now_local_str()
    except Exception:
        from datetime import datetime

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [f"\n[B.A.S.E. SYSTEM OPERATIONS BRIEFING — {now_str}]"]

    try:
        if os.path.exists(_HOST_MONITOR_PATH):
            with open(_HOST_MONITOR_PATH) as f:
                data = json.load(f)

            sys = data.get("system", {})
            cpu = data.get("cpu", {})
            mem = data.get("memory", {})
            disk = data.get("disks", [])
            net = data.get("network", {})
            services = data.get("services", [])

            # System info
            lines.append(
                f"System: {sys.get('hostname', 'archie')} | "
                f"{sys.get('distribution', 'Ubuntu')} | "
                f"Uptime: {sys.get('uptime_formatted', 'unknown')} | "
                f"Processes: {sys.get('process_count', '?')} | "
                f"Load: {'/'.join(str(round(x, 2)) for x in sys.get('load_average', []))}"
            )

            # CPU
            cpu_temp = cpu.get("temperature", 0)
            temp_flag = "⚠️" if cpu_temp and cpu_temp > 75 else ""
            lines.append(
                f"CPU: {cpu.get('model', 'unknown')} | "
                f"{cpu.get('physical_cores', '?')}c/{cpu.get('logical_cores', '?')}t | "
                f"Temp: {cpu_temp}°C {temp_flag}"
            )

            # Memory
            if mem:
                used_gb = round(mem.get("used", 0) / (1024**3), 1)
                total_gb = round(mem.get("total", 0) / (1024**3), 1)
                pct = mem.get("percent", 0)
                mem_flag = "⚠️" if pct > 85 else ""
                lines.append(f"RAM: {used_gb} / {total_gb} GB ({pct}%) {mem_flag}")

            # Disk (first root disk)
            for d in disk[:2]:
                if d.get("mountpoint") in ("/", "/mnt/data2"):
                    used_gb = round(d.get("used", 0) / (1024**3), 1)
                    total_gb = round(d.get("total", 0) / (1024**3), 1)
                    pct = d.get("percent", 0)
                    disk_flag = "⚠️" if pct > 85 else ""
                    lines.append(f"Disk {d.get('mountpoint', '/')}: {used_gb} / {total_gb} GB ({pct}%) {disk_flag}")

            # Services summary
            if services:
                running = sum(1 for s in services if s.get("status") in ("active", "running"))
                failed = sum(1 for s in services if s.get("status") in ("failed", "inactive"))
                lines.append(f"Services: {running} running | {failed} failed/inactive of {len(services)} total")
                if failed > 0:
                    failed_names = [s.get("name", "?") for s in services if s.get("status") in ("failed",)][:3]
                    if failed_names:
                        lines.append(f"  ⚠️ Failed: {', '.join(failed_names)}")
        else:
            lines.append("Live system data: unavailable (host monitor not mounted)")

    except Exception as exc:
        lines.append(f"[Briefing data unavailable: {exc}]")

    # Compliance status from KSO's own DB
    try:
        from db import get_db_cursor  # KSO's own database

        with get_db_cursor() as (conn, cur):
            cur.execute(
                """
                SELECT overall_score, last_scan_at
                FROM compliance_scan_results
                ORDER BY last_scan_at DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            if row:
                score = row.get("overall_score", row[0] if isinstance(row, (list, tuple)) else 0)
                flag = "✅" if score >= 80 else "⚠️"
                lines.append(f"{flag} Compliance: {score:.0f}% (last scan: {row.get('last_scan_at', row[1] if isinstance(row, (list, tuple)) else 'unknown')})")
    except Exception:
        pass  # Compliance data optional

    lines.append(
        "\n[B.A.S.E. SYSTEM OPERATIONS FEATURES]\n"
        "Tabs: Dashboard (CPU/RAM/load/temp) | Storage (disks/volumes) | "
        "Hardware (CPU/RAM specs) | Memory (DIMM slots) | Processes (top tasks) | "
        "Docker (containers/stacks) | Network (interfaces/traffic) | "
        "Firewall (UFW rules) | Compliance (STIG/CIS/HIPAA scans).\n"
        "Back-arrow returns to ARCHIE platform at platform.kytranempowerment.com.\n"
        "[End briefing — use this live data to answer questions about server health. "
        "Propose actions using the [ACTION]{...}[/ACTION] format when asked to act.]\n"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# B.A.S.E. Action Injection
# ---------------------------------------------------------------------------


def _inject_base_action(user_msg: str, agent_resp: str) -> str:
    """Post-process B.A.S.E. response: inject [ACTION] block for KSO system actions."""
    resp_lower = agent_resp.lower()
    user_lower = user_msg.lower()

    if "[action]" in resp_lower or "⟦action⟧" in resp_lower:
        return agent_resp

    if user_msg.strip().startswith("[Action confirmed:"):
        return agent_resp

    action_signal = any(
        kw in resp_lower
        for kw in ["will", "can", "run", "scan", "check", "restart", "clear", "proceed", "action", "execute", "refresh"]
    )
    if not action_signal:
        return agent_resp

    action = None
    if any(kw in user_lower for kw in ["run compliance", "compliance scan", "scan now", "check compliance", "stig scan", "hipaa scan"]):
        action = {
            "type": "run_compliance_scan",
            "label": "Run Compliance Scan",
            "confirm": "Trigger a full compliance scan across all active rule packs",
            "route": "POST /dashboard/api/compliance/run",
            "payload": {},
        }
    elif any(kw in user_lower for kw in ["check services", "service status", "list services", "show services"]):
        action = {
            "type": "check_services",
            "label": "Check Service Status",
            "confirm": "Refresh the list of running system services and their status",
            "route": "GET /dashboard/api/services?filter=all",
            "payload": {},
        }
    elif any(kw in user_lower for kw in ["docker status", "container status", "check docker", "list containers", "docker health"]):
        action = {
            "type": "check_docker",
            "label": "Check Docker Status",
            "confirm": "Fetch current Docker container and stack health",
            "route": "GET /dashboard/api/docker/health",
            "payload": {},
        }
    elif any(kw in user_lower for kw in ["refresh data", "refresh system", "update stats", "reload data", "refresh dashboard"]):
        action = {
            "type": "refresh_system_data",
            "label": "Refresh System Data",
            "confirm": "Force a fresh refresh of all live system metrics",
            "route": "GET /dashboard/api/overview",
            "payload": {},
        }
    elif any(kw in user_lower for kw in ["health alert", "check alerts", "system alert", "show alerts"]):
        action = {
            "type": "check_health_alerts",
            "label": "Check Health Alerts",
            "confirm": "Retrieve all current system health alerts and warnings",
            "route": "GET /dashboard/api/health/alerts",
            "payload": {},
        }

    if action:
        return agent_resp + f"\n\n[ACTION]{json.dumps(action)}[/ACTION]"
    return agent_resp


# ---------------------------------------------------------------------------
# Route Registration
# ---------------------------------------------------------------------------


def register_chat_proxy_routes(app, admin_required_decorator):
    """Register chat proxy directly on the Flask app (not the dashboard blueprint)."""

    @app.route("/tools/archie-chat/api/<path:rest>", methods=["GET", "POST", "PUT", "DELETE"])
    @login_required
    def proxy_archie_chat(rest):
        target = f"{_hub_base()}/tools/archie-chat/api/{rest}"
        is_message_endpoint = rest == "v1/chat/message" and request.method == "POST"

        try:
            body = request.get_data()

            # Inject live briefing for message endpoint
            if is_message_endpoint:
                try:
                    payload = json.loads(body)
                    briefing = _build_kso_briefing()
                    original_msg = payload.get("message", "")
                    payload["message"] = briefing + original_msg
                    payload.setdefault("agent_target", "B.A.S.E.")
                    body = json.dumps(payload).encode()
                except Exception as _be:
                    logger.warning("KSO briefing injection failed (non-fatal): %s", _be)

            hdrs = _hub_headers()
            if is_message_endpoint:
                hdrs["Content-Type"] = "application/json"

            resp = requests.request(
                method=request.method,
                url=target,
                params=request.args,
                data=body,
                headers=hdrs,
                timeout=_PROXY_TIMEOUT,
            )

            # Post-process B.A.S.E. responses to inject action blocks
            if is_message_endpoint and resp.ok:
                try:
                    data = resp.json()
                    if data.get("success") and data.get("response"):
                        original_msg = ""
                        try:
                            original_msg = json.loads(request.get_data()).get("message", "")
                        except Exception:
                            pass
                        data["response"] = _inject_base_action(original_msg, data["response"])
                        return Response(
                            json.dumps(data),
                            status=resp.status_code,
                            content_type="application/json",
                        )
                except Exception:
                    pass

            return Response(
                resp.content,
                status=resp.status_code,
                content_type=resp.headers.get("Content-Type", "application/json"),
            )
        except requests.Timeout:
            logger.warning("Chat proxy timeout for %s", target)
            return Response(b'{"success":false,"error":"timeout"}', 504, content_type="application/json")
        except Exception as exc:
            logger.exception("Chat proxy error: %s", exc)
            return Response(b'{"success":false,"error":"proxy error"}', 502, content_type="application/json")

    @app.route("/tools/media-studio/api/portraits/<int:agent_id>", methods=["GET"])
    @login_required
    def proxy_agent_portrait(agent_id):
        hub_url = _hub_base()
        try:
            resp = requests.get(f"{hub_url}/api/public/directory", timeout=10)
            if resp.ok:
                for agent in resp.json().get("agents", []):
                    if agent.get("id") == agent_id:
                        portrait = agent.get("avatar_thumb_url") or agent.get("avatar_url")
                        if portrait:
                            return Response(
                                json.dumps({"canonical_image_path": portrait, "avatar_url": portrait}),
                                200,
                                content_type="application/json",
                            )
        except Exception:
            pass
        return Response(b'{"canonical_image_path":null,"avatar_url":null}', 200, content_type="application/json")

    @app.route("/kso/platform-static/<path:static_path>", methods=["GET"])
    @login_required
    def proxy_platform_static(static_path):
        hub_url = _hub_base()
        try:
            resp = requests.get(f"{hub_url}/static/{static_path}", timeout=10)
            return Response(
                resp.content,
                status=resp.status_code,
                content_type=resp.headers.get("Content-Type", "image/png"),
            )
        except Exception:
            return Response(b"", 404)
