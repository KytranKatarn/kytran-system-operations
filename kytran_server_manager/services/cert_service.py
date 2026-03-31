"""Cert sync service — pull wildcard cert from hub, save locally."""

import hashlib
import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

HUB_URL = os.environ.get("KSM_HUB_URL", "http://100.64.0.2:3000")
NODE_API_KEY = os.environ.get("KSM_HUB_API_KEY", "")
LOCAL_CERT_DIR = os.environ.get("KSM_CERT_DIR", "/opt/archie-fleet/certs/wildcard")
SYNC_INTERVAL = 43200  # 12 hours


def get_local_cert_info():
    """Return local cert hash and expiry, or None if no cert."""
    cert_path = os.path.join(LOCAL_CERT_DIR, "fullchain.pem")
    if not os.path.exists(cert_path):
        return None
    try:
        with open(cert_path, "rb") as f:
            cert_pem = f.read()
        return {"sha256": hashlib.sha256(cert_pem).hexdigest()}
    except Exception:
        return None


def check_and_sync_cert():
    """Check hub for newer cert, download if available. Returns True if updated."""
    try:
        # 1. Check hub cert metadata
        resp = requests.get(
            f"{HUB_URL}/tools/starbase/api/fleet/certs/wildcard/check",
            headers={"X-Node-API-Key": NODE_API_KEY},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"[CertSync] Hub cert check failed: {resp.status_code}")
            return False

        hub_info = resp.json()
        if not hub_info.get("success"):
            return False

        # 2. Compare with local cert
        local_info = get_local_cert_info()
        if local_info and local_info["sha256"] == hub_info["sha256"]:
            logger.debug("[CertSync] Local cert matches hub, no update needed")
            return False

        # 3. Download full cert
        resp = requests.get(
            f"{HUB_URL}/tools/starbase/api/fleet/certs/wildcard",
            headers={"X-Node-API-Key": NODE_API_KEY},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"[CertSync] Hub cert download failed: {resp.status_code}")
            return False

        data = resp.json()
        if not data.get("success"):
            return False

        # 4. Save locally
        os.makedirs(LOCAL_CERT_DIR, exist_ok=True)

        cert_path = os.path.join(LOCAL_CERT_DIR, "fullchain.pem")
        key_path = os.path.join(LOCAL_CERT_DIR, "privkey.pem")

        with open(cert_path, "w") as f:
            f.write(data["cert_pem"])
        os.chmod(cert_path, 0o644)

        with open(key_path, "w") as f:
            f.write(data["key_pem"])
        os.chmod(key_path, 0o600)

        logger.info(f"[CertSync] Updated wildcard cert, expires {data['expires_at']}")
        return True

    except Exception as e:
        logger.warning(f"[CertSync] Sync failed: {e}")
        return False


def start_cert_sync_thread():
    """Start background thread that syncs cert every 12h."""

    def _sync_loop():
        # Initial sync on startup (wait 30s for hub connectivity)
        time.sleep(30)
        check_and_sync_cert()

        while True:
            time.sleep(SYNC_INTERVAL)
            check_and_sync_cert()

    thread = threading.Thread(target=_sync_loop, daemon=True, name="cert-sync")
    thread.start()
    logger.info("[CertSync] Background cert sync started (interval: 12h)")
