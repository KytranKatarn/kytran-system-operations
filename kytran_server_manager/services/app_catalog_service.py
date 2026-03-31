"""App Catalog service — fetch registry, deploy/manage Docker containers."""

import json
import os
import secrets
import shutil
import subprocess
import time

import requests
import yaml


APPS_DIR = os.environ.get("KSM_APPS_DIR", "/opt/archie-fleet/apps")
HUB_URL = os.environ.get("KSM_HUB_URL", "http://100.64.0.2:3000")
NODE_TYPE = os.environ.get("KSM_NODE_TYPE", "outpost")
CATALOG_CACHE_FILE = os.path.join(APPS_DIR, ".catalog-cache.json")
CATALOG_CACHE_TTL = 1800  # 30 minutes


def get_catalog(force_refresh=False):
    """Fetch app catalog from hub. Uses local cache if fresh."""
    os.makedirs(APPS_DIR, exist_ok=True)

    if not force_refresh and os.path.exists(CATALOG_CACHE_FILE):
        try:
            with open(CATALOG_CACHE_FILE) as f:
                cache = json.load(f)
            if time.time() - cache.get("fetched_at", 0) < CATALOG_CACHE_TTL:
                return cache.get("apps", [])
        except Exception:
            pass

    try:
        resp = requests.get(
            f"{HUB_URL}/starbase/api/fleet/catalog",
            params={"node_type": NODE_TYPE},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            apps = data.get("apps", [])
            with open(CATALOG_CACHE_FILE, "w") as f:
                json.dump({"fetched_at": time.time(), "apps": apps}, f)
            return apps
    except Exception:
        pass

    if os.path.exists(CATALOG_CACHE_FILE):
        try:
            with open(CATALOG_CACHE_FILE) as f:
                return json.load(f).get("apps", [])
        except Exception:
            pass

    return []


def get_installed_apps():
    """List apps deployed on this node with their container status."""
    installed = []
    if not os.path.isdir(APPS_DIR):
        return installed

    for app_id in sorted(os.listdir(APPS_DIR)):
        app_dir = os.path.join(APPS_DIR, app_id)
        compose_file = os.path.join(app_dir, "docker-compose.yml")
        if not os.path.isfile(compose_file):
            continue

        meta_file = os.path.join(app_dir, ".app-meta.json")
        meta = {}
        if os.path.exists(meta_file):
            try:
                with open(meta_file) as f:
                    meta = json.load(f)
            except Exception:
                pass

        status = _get_container_status(app_dir)
        installed.append(
            {
                "id": app_id,
                "name": meta.get("name", app_id),
                "version": meta.get("version", "unknown"),
                "image": meta.get("image", "unknown"),
                "port": meta.get("port"),
                "status": status,
                "installed_at": meta.get("installed_at"),
                "health_endpoint": meta.get("health_endpoint"),
            }
        )

    return installed


def deploy_app(app_id, catalog_entry, env_overrides=None):
    """Deploy an app from catalog entry. Returns (success, message)."""
    app_dir = os.path.join(APPS_DIR, app_id)
    os.makedirs(app_dir, exist_ok=True)

    compose = _generate_compose(app_id, catalog_entry)
    compose_path = os.path.join(app_dir, "docker-compose.yml")
    with open(compose_path, "w") as f:
        yaml.dump(compose, f, default_flow_style=False)

    env_lines = _generate_env(catalog_entry, env_overrides)
    env_path = os.path.join(app_dir, ".env")
    with open(env_path, "w") as f:
        f.write("\n".join(env_lines) + "\n")

    meta = {
        "name": catalog_entry.get("name", app_id),
        "version": catalog_entry.get("version", "unknown"),
        "image": catalog_entry.get("image", "unknown"),
        "port": catalog_entry.get("port"),
        "health_endpoint": catalog_entry.get("health_endpoint"),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(os.path.join(app_dir, ".app-meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    try:
        subprocess.run(
            ["docker", "compose", "pull"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return False, f"docker compose up failed: {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, "Docker operation timed out"
    except Exception as e:
        return False, str(e)

    return True, "App deployed successfully"


def stop_app(app_id):
    """Stop a deployed app."""
    app_dir = os.path.join(APPS_DIR, app_id)
    if not os.path.isdir(app_dir):
        return False, "App not installed"
    try:
        result = subprocess.run(
            ["docker", "compose", "stop"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return False, result.stderr
    except Exception as e:
        return False, str(e)
    return True, "App stopped"


def start_app(app_id):
    """Start a stopped app."""
    app_dir = os.path.join(APPS_DIR, app_id)
    if not os.path.isdir(app_dir):
        return False, "App not installed"
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return False, result.stderr
    except Exception as e:
        return False, str(e)
    return True, "App started"


def remove_app(app_id, keep_data=False):
    """Remove a deployed app. Optionally keep data volumes."""
    app_dir = os.path.join(APPS_DIR, app_id)
    if not os.path.isdir(app_dir):
        return False, "App not installed"

    try:
        subprocess.run(
            ["docker", "compose", "down", "--remove-orphans"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception:
        pass

    if keep_data:
        for f in ("docker-compose.yml", ".env", ".app-meta.json"):
            path = os.path.join(app_dir, f)
            if os.path.exists(path):
                os.remove(path)
    else:
        shutil.rmtree(app_dir, ignore_errors=True)

    return True, "App removed"


def update_app(app_id, catalog_entry):
    """Update app to latest image. Pull new image, recreate container."""
    app_dir = os.path.join(APPS_DIR, app_id)
    if not os.path.isdir(app_dir):
        return False, "App not installed"

    try:
        subprocess.run(
            ["docker", "compose", "pull"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        result = subprocess.run(
            ["docker", "compose", "up", "-d", "--force-recreate"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return False, result.stderr
    except subprocess.TimeoutExpired:
        return False, "Docker operation timed out"
    except Exception as e:
        return False, str(e)

    meta_file = os.path.join(app_dir, ".app-meta.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file) as f:
                meta = json.load(f)
            meta["version"] = catalog_entry.get("version", meta.get("version"))
            meta["image"] = catalog_entry.get("image", meta.get("image"))
            with open(meta_file, "w") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            pass

    return True, "App updated"


def get_app_logs(app_id, lines=100):
    """Get container logs for a deployed app."""
    app_dir = os.path.join(APPS_DIR, app_id)
    if not os.path.isdir(app_dir):
        return None
    try:
        result = subprocess.run(
            ["docker", "compose", "logs", "--tail", str(lines), "--no-color"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except Exception:
        return ""


def check_app_health(app_id):
    """Check health endpoint for a deployed app."""
    app_dir = os.path.join(APPS_DIR, app_id)
    meta_file = os.path.join(app_dir, ".app-meta.json")
    if not os.path.exists(meta_file):
        return "unknown"

    try:
        with open(meta_file) as f:
            meta = json.load(f)
    except Exception:
        return "unknown"

    endpoint = meta.get("health_endpoint")
    port = meta.get("port")
    if not endpoint or not port:
        return "no_health_check"

    try:
        resp = requests.get(f"http://localhost:{port}{endpoint}", timeout=5)
        return "healthy" if resp.status_code == 200 else "unhealthy"
    except Exception:
        return "unreachable"


# ── Internal helpers ─────────────────────────────────────────────────


def _generate_compose(app_id, entry):
    """Generate a docker-compose.yml dict from a catalog entry."""
    service = {
        "image": entry["image"],
        "container_name": f"archie_fleet_{app_id}",
        "restart": "unless-stopped",
        "env_file": [".env"],
        "deploy": {
            "resources": {
                "limits": {
                    "memory": f"{entry.get('resources', {}).get('min_ram_mb', 256)}M",
                }
            }
        },
    }

    port = entry.get("port")
    if port:
        service["ports"] = [f"{port}:{port}"]

    volumes = entry.get("volumes", [])
    if volumes:
        service["volumes"] = [f"./data/{v['name']}:{v['path']}" for v in volumes]

    return {"services": {app_id: service}}


def _generate_env(entry, overrides=None):
    """Generate .env lines from catalog entry env definitions."""
    lines = []
    overrides = overrides or {}

    for env_def in entry.get("env", []):
        name = env_def["name"]
        if name in overrides:
            value = overrides[name]
        elif env_def.get("generate"):
            value = secrets.token_urlsafe(32)
        else:
            value = env_def.get("default", "")
        lines.append(f"{name}={value}")

    return lines


def _get_container_status(app_dir):
    """Get Docker container status for an app."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return "error"
        output = result.stdout.strip()
        if not output:
            return "stopped"
        for line in output.splitlines():
            try:
                container = json.loads(line)
                state = container.get("State", "unknown")
                return "running" if state == "running" else state
            except json.JSONDecodeError:
                continue
        return "unknown"
    except Exception:
        return "unknown"
