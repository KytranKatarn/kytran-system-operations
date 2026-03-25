"""NPM (Nginx Proxy Manager) integration routes for standalone."""

from flask import jsonify
import requests
import os


NPM_BASE = os.getenv("NPM_API_URL", "http://192.168.1.200:81/api")
NPM_TOKEN = None


def _get_npm_token():
    """Get or refresh NPM API token."""
    global NPM_TOKEN
    if NPM_TOKEN:
        return NPM_TOKEN
    email = os.getenv("NPM_EMAIL", "")
    password = os.getenv("NPM_PASSWORD", "")
    if not email or not password:
        return None
    try:
        resp = requests.post(
            f"{NPM_BASE}/tokens",
            json={"identity": email, "secret": password},
            timeout=10,
        )
        if resp.status_code == 200:
            NPM_TOKEN = resp.json().get("token")
            return NPM_TOKEN
    except Exception:
        pass
    return None


def _npm_headers():
    """Build auth headers for NPM API."""
    token = _get_npm_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def register_proxy_routes(bp, admin_required):
    @bp.route("/api/proxy/hosts", methods=["GET"])
    @admin_required
    def proxy_hosts():
        """List all proxy hosts from NPM."""
        try:
            resp = requests.get(
                f"{NPM_BASE}/nginx/proxy-hosts",
                headers=_npm_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return jsonify({"success": True, "hosts": resp.json()})
            return jsonify({"success": False, "error": f"NPM returned {resp.status_code}"}), resp.status_code
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/proxy/hosts/<int:host_id>", methods=["GET"])
    @admin_required
    def proxy_host_detail(host_id):
        """Get single proxy host details."""
        try:
            resp = requests.get(
                f"{NPM_BASE}/nginx/proxy-hosts/{host_id}",
                headers=_npm_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return jsonify({"success": True, "host": resp.json()})
            return jsonify({"success": False, "error": f"NPM returned {resp.status_code}"}), resp.status_code
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/proxy/certificates", methods=["GET"])
    @admin_required
    def proxy_certificates():
        """List SSL certificates from NPM."""
        try:
            resp = requests.get(
                f"{NPM_BASE}/nginx/certificates",
                headers=_npm_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return jsonify({"success": True, "certificates": resp.json()})
            return jsonify({"success": False, "error": f"NPM returned {resp.status_code}"}), resp.status_code
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/api/proxy/status", methods=["GET"])
    @admin_required
    def proxy_status():
        """Check NPM connectivity."""
        try:
            token = _get_npm_token()
            if not token:
                return jsonify({
                    "success": False,
                    "status": "disconnected",
                    "error": "No NPM credentials configured. Set NPM_EMAIL and NPM_PASSWORD env vars.",
                })
            resp = requests.get(
                f"{NPM_BASE}/nginx/proxy-hosts",
                headers=_npm_headers(),
                timeout=5,
            )
            return jsonify({
                "success": True,
                "status": "connected",
                "host_count": len(resp.json()) if resp.status_code == 200 else 0,
            })
        except Exception as e:
            return jsonify({"success": False, "status": "error", "error": str(e)})
