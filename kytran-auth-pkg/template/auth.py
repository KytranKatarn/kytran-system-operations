"""Kytran Auth — tier-based access control template.

Drop this into any Flask standalone product alongside kytran_auth.py.
Configure PRODUCT_CLIENT_ID to match your OAuth client_id.

Usage:
    from auth import tier_required, admin_required, auth_bp
    app.register_blueprint(auth_bp)
"""

import os
from functools import wraps

from flask import Blueprint, jsonify, redirect, request, session, url_for

auth_bp = Blueprint("auth", __name__)

# Product-specific: set via env var or override here
PRODUCT_CLIENT_ID = os.environ.get("KYTRAN_CLIENT_ID", "")

# Tier hierarchy (higher index = more access)
TIER_LEVELS = {"free": 0, "viewer": 1, "pro": 2}


def resolve_tier(user):
    """Resolve the highest tier from user entitlements."""
    if not user:
        return "free"
    entitlements = user.get("entitlements", [])
    # Check for product-specific tiers: {client_id}-pro, {client_id}-viewer
    base = PRODUCT_CLIENT_ID.replace("-viewer", "").replace("-pro", "")
    if f"{base}-pro" in entitlements:
        return "pro"
    if f"{base}-viewer" in entitlements or PRODUCT_CLIENT_ID in entitlements:
        return "viewer"
    # Admin gets full access
    if user.get("role") == "admin":
        return "pro"
    return "free"


def get_current_tier():
    """Get the current user's tier from session."""
    user = session.get("kytran_user")
    return resolve_tier(user)


def is_admin():
    """Check if the current user is an admin."""
    user = session.get("kytran_user")
    if not user:
        return False
    return user.get("role") == "admin"


def tier_required(min_tier):
    """Decorator: require a minimum subscription tier.

    Usage:
        @tier_required("viewer")   # viewer + pro + admin
        @tier_required("pro")      # pro + admin only
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = session.get("kytran_user")
            if not user:
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"success": False, "error": "Authentication required"}), 401
                return redirect(url_for("kytran_login", next=request.url))

            current_tier = resolve_tier(user)
            current_level = TIER_LEVELS.get(current_tier, 0)
            required_level = TIER_LEVELS.get(min_tier, 0)

            if current_level < required_level:
                return jsonify({
                    "success": False,
                    "error": f"Requires {min_tier} tier",
                    "current_tier": current_tier,
                }), 403

            return f(*args, **kwargs)
        return decorated
    return decorator


def admin_required(f):
    """Decorator: require admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin():
            return jsonify({"success": False, "error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


# --- Routes ---

@auth_bp.route("/api/me")
def api_me():
    """Return current user profile and tier."""
    user = session.get("kytran_user")
    if not user:
        return jsonify({"authenticated": False, "tier": "free"})
    return jsonify({
        "authenticated": True,
        "username": user.get("username"),
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role"),
        "tier": resolve_tier(user),
        "is_admin": user.get("role") == "admin",
    })


@auth_bp.route("/auth/logout")
def logout():
    """Clear session and redirect to landing."""
    session.clear()
    return redirect("/")
