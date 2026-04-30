"""Billing gate — checks access_grants via ARCHIE Account Center API."""
import logging
import os
import requests

logger = logging.getLogger(__name__)

_AC_BASE = os.getenv("AC_API_URL", "http://192.168.1.200:3000")
_AC_INTERNAL_TOKEN = os.getenv("AC_INTERNAL_TOKEN", "")
PRODUCT_SLUG = "kso-compliance"


def has_product_access(user_id: int) -> bool:
    """Return True if user has access to kso-compliance. Fail-open on error."""
    try:
        headers = {}
        if _AC_INTERNAL_TOKEN:
            headers["X-Internal-Token"] = _AC_INTERNAL_TOKEN
        resp = requests.get(
            f"{_AC_BASE}/tools/account-center/api/access/check",
            params={"user_id": user_id, "product": PRODUCT_SLUG},
            headers=headers,
            timeout=5,
        )
        return resp.json().get("has_access", False)
    except Exception as exc:
        logger.warning("[BillingGate] kso-compliance check failed for user %s: %s", user_id, exc)
        return False
