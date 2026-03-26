"""ARCHIE Hub client for compliance reporting and SSO token exchange."""

import base64
import logging

import requests
from flask import current_app

logger = logging.getLogger(__name__)

# Timeout for hub requests (seconds)
_REQUEST_TIMEOUT = 10


def is_hub_configured() -> bool:
    """Check if ARCHIE hub connection is configured."""
    url = current_app.config.get("ARCHIE_HUB_URL", "")
    secret = current_app.config.get("ARCHIE_CLIENT_SECRET", "")
    return bool(url and secret)


def _auth_header() -> dict:
    """Build base64-encoded Authorization header from client credentials."""
    client_id = current_app.config.get("ARCHIE_CLIENT_ID", "kytran-sysops")
    client_secret = current_app.config.get("ARCHIE_CLIENT_SECRET", "")
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Bearer {encoded}", "Content-Type": "application/json"}


def report_compliance(scan_result: dict) -> bool:
    """POST compliance scan results to the ARCHIE hub.

    Args:
        scan_result: Dict with keys like overall_score, pack_scores,
                     total_rules, passed, failed.

    Returns:
        True on success, False on failure or if hub is unreachable.
    """
    if not is_hub_configured():
        logger.debug("Hub not configured — skipping compliance report")
        return False

    hub_url = current_app.config["ARCHIE_HUB_URL"].rstrip("/")
    endpoint = f"{hub_url}/api/standalone/compliance-report"

    payload = {
        "product_name": "kytran-server-manager",
        "overall_score": scan_result.get("overall_score", 0),
        "pack_scores": scan_result.get("pack_scores", {}),
        "total_rules": scan_result.get("total_rules", 0),
        "passed": scan_result.get("passed", 0),
        "failed": scan_result.get("failed", 0),
    }

    try:
        resp = requests.post(
            endpoint, json=payload, headers=_auth_header(), timeout=_REQUEST_TIMEOUT
        )
        if resp.ok:
            logger.info("Compliance report sent to hub (score=%s)", payload["overall_score"])
            return True
        else:
            logger.warning(
                "Hub rejected compliance report: %s %s", resp.status_code, resp.text[:200]
            )
            return False
    except requests.ConnectionError:
        logger.debug("Hub unreachable at %s — compliance report skipped", hub_url)
        return False
    except requests.Timeout:
        logger.debug("Hub request timed out — compliance report skipped")
        return False
    except Exception:
        logger.exception("Unexpected error reporting compliance to hub")
        return False


def fetch_ai_analysis() -> dict | None:
    """Fetch AI-powered compliance analysis from the ARCHIE hub.

    Returns:
        Analysis dict on success, None on failure or if hub is unreachable.
    """
    if not is_hub_configured():
        logger.debug("Hub not configured — skipping AI analysis fetch")
        return None

    hub_url = current_app.config["ARCHIE_HUB_URL"].rstrip("/")
    endpoint = f"{hub_url}/api/standalone/compliance-analysis"

    try:
        resp = requests.get(
            endpoint,
            params={"scan_target": "Kytran System Operations"},
            headers=_auth_header(),
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.ok:
            logger.info("AI compliance analysis fetched from hub")
            return resp.json()
        else:
            logger.warning(
                "Hub rejected AI analysis request: %s %s", resp.status_code, resp.text[:200]
            )
            return None
    except requests.ConnectionError:
        logger.debug("Hub unreachable at %s — AI analysis fetch skipped", hub_url)
        return None
    except requests.Timeout:
        logger.debug("Hub request timed out — AI analysis fetch skipped")
        return None
    except Exception:
        logger.exception("Unexpected error fetching AI analysis from hub")
        return None


def create_checkout_session(price_id: str, success_url: str, cancel_url: str, user_id=None) -> str | None:
    """Create a Stripe Checkout session via the ARCHIE hub.

    Args:
        price_id: Stripe price ID for the selected plan.
        success_url: URL to redirect to after successful payment.
        cancel_url: URL to redirect to if the user cancels.
        user_id: Optional user identifier to associate with the session.

    Returns:
        Checkout URL string on success, None on failure.
    """
    if not is_hub_configured():
        logger.warning("Hub not configured — cannot create checkout session")
        return None

    hub_url = current_app.config["ARCHIE_HUB_URL"].rstrip("/")
    endpoint = f"{hub_url}/api/billing/checkout"

    payload = {
        "price_id": price_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    if user_id is not None:
        payload["user_id"] = user_id

    try:
        resp = requests.post(
            endpoint, json=payload, headers=_auth_header(), timeout=_REQUEST_TIMEOUT
        )
        if resp.ok:
            data = resp.json()
            url = data.get("checkout_url") or data.get("url")
            logger.info("Checkout session created via hub (price=%s)", price_id)
            return url
        else:
            logger.warning(
                "Hub rejected checkout request: %s %s", resp.status_code, resp.text[:200]
            )
            return None
    except requests.ConnectionError:
        logger.debug("Hub unreachable at %s — checkout session failed", hub_url)
        return None
    except requests.Timeout:
        logger.debug("Hub request timed out — checkout session failed")
        return None
    except Exception:
        logger.exception("Unexpected error creating checkout session via hub")
        return None


def exchange_code_for_token(code: str, redirect_uri: str) -> dict | None:
    """Exchange an OAuth authorization code for a JWT token.

    Args:
        code: The authorization code received from the hub.
        redirect_uri: The redirect URI used in the original auth request.

    Returns:
        Token response dict on success, None on failure.
    """
    if not is_hub_configured():
        logger.warning("Hub not configured — cannot exchange OAuth code")
        return None

    hub_url = current_app.config["ARCHIE_HUB_URL"].rstrip("/")
    endpoint = f"{hub_url}/oauth/token"

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": current_app.config.get("ARCHIE_CLIENT_ID", "kytran-sysops"),
        "client_secret": current_app.config.get("ARCHIE_CLIENT_SECRET", ""),
    }

    try:
        resp = requests.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.ok:
            logger.info("OAuth token exchange successful")
            return resp.json()
        else:
            logger.warning(
                "OAuth token exchange failed: %s %s", resp.status_code, resp.text[:200]
            )
            return None
    except requests.ConnectionError:
        logger.debug("Hub unreachable at %s — token exchange failed", hub_url)
        return None
    except requests.Timeout:
        logger.debug("Hub request timed out — token exchange failed")
        return None
    except Exception:
        logger.exception("Unexpected error during OAuth token exchange")
        return None
