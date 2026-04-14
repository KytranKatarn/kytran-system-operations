"""Application configuration."""
import os


class Config:
    SECRET_KEY = os.environ.get("KSO_SECRET_KEY", "change-me-in-production")
    # Use /var/lib for system installs, ~/.kytran-system-operations for user installs
    _default_data = os.environ.get(
        "KSO_DATA_DIR",
        "/var/lib/kytran-system-operations" if os.getuid() == 0
        else os.path.join(os.path.expanduser("~"), ".kytran-system-operations")
    )
    DATA_DIR = _default_data
    DB_PATH = os.path.join(DATA_DIR, "data.db")
    THEME = os.environ.get("KSO_THEME", "kytran")
    HOST = os.environ.get("KSO_HOST", "0.0.0.0")
    PORT = int(os.environ.get("KSO_PORT", "8080"))
    DEBUG = os.environ.get("KSO_DEBUG", "false").lower() == "true"
    BASE_DIR = os.environ.get("KSO_BASE_DIR", "/")

    # ARCHIE Hub connection (for SSO + compliance reporting)
    ARCHIE_HUB_URL = os.environ.get("KSO_ARCHIE_HUB_URL", "")  # e.g., https://archie.example.com
    ARCHIE_CLIENT_ID = os.environ.get("KSO_ARCHIE_CLIENT_ID", "kytran-sysops")
    ARCHIE_CLIENT_SECRET = os.environ.get("KSO_ARCHIE_CLIENT_SECRET", "")
    SERVER_SUBDOMAIN = os.environ.get("KSO_SERVER_SUBDOMAIN", "")  # e.g., server.kytranempowerment.com

    # Compliance scanning schedule
    COMPLIANCE_SCAN_INTERVAL = int(os.environ.get("KSO_COMPLIANCE_SCAN_INTERVAL", "21600"))  # 6 hours in seconds

    # Subscription tier override (set to "enterprise" for internal ARCHIE instance)
    TIER_OVERRIDE = os.environ.get("KSO_TIER_OVERRIDE", "")

    # Version
    VERSION = os.environ.get("KSO_VERSION", "1.0.0")
