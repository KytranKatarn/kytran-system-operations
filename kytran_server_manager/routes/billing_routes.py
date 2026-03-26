"""Billing routes — tier update receiver and checkout client."""

import base64
import logging

from flask import current_app, jsonify, request, url_for
from flask_login import current_user, login_required

from ..services.hub_client import create_checkout_session
from ..services.subscription_service import set_user_tier

logger = logging.getLogger(__name__)


def _verify_hub_credentials():
    """Validate the incoming Bearer token against configured client credentials.

    Expected header: ``Authorization: Bearer base64(client_id:client_secret)``

    Returns:
        True if credentials match, False otherwise.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False

    token = auth[7:]
    try:
        decoded = base64.b64decode(token).decode()
    except Exception:
        return False

    expected_id = current_app.config.get("ARCHIE_CLIENT_ID", "kytran-sysops")
    expected_secret = current_app.config.get("ARCHIE_CLIENT_SECRET", "")
    if not expected_secret:
        logger.warning("ARCHIE_CLIENT_SECRET not configured — rejecting tier update")
        return False

    return decoded == f"{expected_id}:{expected_secret}"


def register_billing_routes(bp, admin_required):  # noqa: ARG001 — admin_required unused but kept for pattern
    """Register billing-related routes on *bp*."""

    # ------------------------------------------------------------------
    # Tier update receiver (called by ARCHIE hub after Stripe webhook)
    # ------------------------------------------------------------------
    @bp.route("/api/billing/tier-update", methods=["POST"])
    def billing_tier_update():
        if not _verify_hub_credentials():
            return jsonify({"error": "unauthorized"}), 401

        data = request.get_json(silent=True) or {}
        user_id = data.get("user_id")
        tier = data.get("tier")

        if not user_id or not tier:
            return jsonify({"error": "user_id and tier are required"}), 400

        try:
            set_user_tier(user_id, tier)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        logger.info("Tier updated via hub callback: user=%s tier=%s", user_id, tier)
        return jsonify({"status": "ok", "user_id": user_id, "tier": tier}), 200

    # ------------------------------------------------------------------
    # Checkout session creator (called by logged-in user)
    # ------------------------------------------------------------------
    @bp.route("/api/billing/checkout", methods=["POST"])
    @login_required
    def billing_checkout():
        data = request.get_json(silent=True) or {}
        price_id = data.get("price_id")
        if not price_id:
            return jsonify({"error": "price_id is required"}), 400

        success_url = url_for("sysops.billing_success", _external=True)
        cancel_url = url_for("sysops.billing_cancel", _external=True)

        checkout_url = create_checkout_session(
            price_id=price_id,
            success_url=success_url,
            cancel_url=cancel_url,
            user_id=getattr(current_user, "id", None),
        )

        if checkout_url is None:
            return jsonify({"error": "Unable to create checkout session"}), 502

        return jsonify({"checkout_url": checkout_url}), 200

    # ------------------------------------------------------------------
    # Simple success / cancel landing pages
    # ------------------------------------------------------------------
    @bp.route("/billing/success")
    def billing_success():
        return _lcars_page(
            title="Payment Successful!",
            message="Your subscription has been activated. You can close this page.",
            accent="#00e5ff",
        )

    @bp.route("/billing/cancel")
    def billing_cancel():
        return _lcars_page(
            title="Payment Cancelled",
            message="No charges were made. You can return to the dashboard to try again.",
            accent="#ef4444",
        )


def _lcars_page(title: str, message: str, accent: str = "#00e5ff") -> str:
    """Return a minimal dark LCARS-themed HTML page."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Kytran Server Manager</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #0a0e1a;
    color: #c8d6e5;
    font-family: 'Segoe UI', system-ui, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
  }}
  .card {{
    background: #111827;
    border: 1px solid {accent}44;
    border-radius: 12px;
    padding: 3rem 2.5rem;
    max-width: 480px;
    text-align: center;
  }}
  .card h1 {{
    color: {accent};
    font-size: 1.8rem;
    margin-bottom: 1rem;
  }}
  .card p {{
    font-size: 1.05rem;
    line-height: 1.6;
    opacity: 0.85;
  }}
  .glow {{
    width: 60px; height: 60px;
    border-radius: 50%;
    background: {accent}22;
    border: 2px solid {accent};
    margin: 0 auto 1.5rem;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.6rem;
  }}
</style>
</head>
<body>
  <div class="card">
    <div class="glow">{"&#10003;" if "Success" in title else "&#10007;"}</div>
    <h1>{title}</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""
    return html, 200, {"Content-Type": "text/html"}
