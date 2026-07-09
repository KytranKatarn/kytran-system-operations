"""Cross-node Docker visibility (#3763).

Aggregates running containers across the mesh: the hub (this host's Docker
socket, mounted into KSO) plus every approved/active remote node via its
node_api `/diagnostics` endpoint (node_api port 3001). Read-only; per-node
failures are isolated so one offline node never breaks the whole view.
"""
import os
import json
import socket
import logging
import http.client
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

NODE_API_PORT = int(os.environ.get("NODE_API_PORT", "3001"))
_NODE_TIMEOUT = 8
_DOCKER_SOCK = "/var/run/docker.sock"


def _hub_containers():
    """List the hub's own containers via the mounted Docker socket."""
    if not os.path.exists(_DOCKER_SOCK):
        return [], "docker socket not mounted"
    try:
        conn = http.client.HTTPConnection("localhost", timeout=5)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(_DOCKER_SOCK)
        conn.sock = s
        conn.request("GET", "/containers/json?all=0")
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status != 200:
            return [], f"docker HTTP {resp.status}"
        out = []
        for ct in (json.loads(raw) or []):
            out.append({
                "name": (ct.get("Names") or ["?"])[0].lstrip("/"),
                "image": ct.get("Image", ""),
                "state": ct.get("State", ""),
                "status": ct.get("Status", ""),
            })
        return out, None
    except Exception as e:
        return [], str(e)[:160]


def _node_containers(host):
    """Fetch a remote node's containers from its node_api /diagnostics."""
    import requests
    try:
        r = requests.get(f"http://{host}:{NODE_API_PORT}/diagnostics", timeout=_NODE_TIMEOUT)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        return (r.json() or {}).get("containers", []), None
    except Exception as e:
        return None, str(e)[:160]


def _parse_env_hosts(env):
    """Parse FLEET_NODE_HOSTS: comma-separated "Name@meship" or bare "meship"."""
    out = []
    for item in (env or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "@" in item:
            name, host = item.split("@", 1)
        else:
            name = host = item
        name, host = name.strip(), host.strip()
        if host:
            out.append({"node": name or host, "host": host})
    return out


def _roster():
    """Remote mesh nodes to poll for containers.

    Source 1 (primary, mesh-correct): FLEET_NODE_HOSTS env —
        e.g. "Outpost-US@100.64.0.2,Starship-246@100.64.0.3".
        KSO is standalone; its own DB has no mesh_ip column, so explicit
        mesh IPs via env are the canonical source (never public hostnames).
    Source 2 (fallback): local remote_nodes table when populated.
        Columns are name/hostname/status (NOT node_name/is_active).
    """
    env_nodes = _parse_env_hosts(os.environ.get("FLEET_NODE_HOSTS", ""))
    if env_nodes:
        return env_nodes

    nodes = []
    try:
        from database import get_db_cursor
        with get_db_cursor() as (conn, cur):
            cur.execute(
                "SELECT name, hostname, status FROM remote_nodes ORDER BY name"
            )
            for row in cur.fetchall():
                if (row.get("status") or "").lower() in (
                    "offline", "decommissioned", "rejected"
                ):
                    continue
                if row.get("hostname"):
                    nodes.append({"node": row["name"] or row["hostname"],
                                  "host": row["hostname"]})
    except Exception:
        logger.exception("fleet_docker: remote_nodes roster query failed")
    return nodes


def get_fleet_containers():
    """Aggregate containers across hub + all mesh nodes."""
    hub_cts, hub_err = _hub_containers()
    result = [{"node": "Hub (local)", "host": "localhost", "online": hub_err is None,
               "error": hub_err, "containers": hub_cts}]

    roster = _roster()
    if roster:
        with ThreadPoolExecutor(max_workers=min(8, len(roster))) as ex:
            futs = {ex.submit(_node_containers, n["host"]): n for n in roster}
            for fut in as_completed(futs):
                n = futs[fut]
                cts, err = fut.result()
                result.append({"node": n["node"], "host": n["host"],
                               "online": err is None, "error": err,
                               "containers": cts or []})

    result.sort(key=lambda x: (x["node"] != "Hub (local)", x["node"].lower()))
    return {
        "success": True,
        "nodes": result,
        "node_count": len(result),
        "online_count": sum(1 for n in result if n["online"]),
        "total_containers": sum(len(n["containers"]) for n in result),
    }
