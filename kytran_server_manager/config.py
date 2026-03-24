"""Application configuration."""
import os


class Config:
    SECRET_KEY = os.environ.get("KSM_SECRET_KEY", "change-me-in-production")
    DATA_DIR = os.environ.get("KSM_DATA_DIR", os.path.expanduser("~/.kytran-server-manager"))
    DB_PATH = os.path.join(DATA_DIR, "data.db")
    THEME = os.environ.get("KSM_THEME", "kytran")
    HOST = os.environ.get("KSM_HOST", "0.0.0.0")
    PORT = int(os.environ.get("KSM_PORT", "8080"))
    DEBUG = os.environ.get("KSM_DEBUG", "false").lower() == "true"
    BASE_DIR = os.environ.get("KSM_BASE_DIR", "/")
