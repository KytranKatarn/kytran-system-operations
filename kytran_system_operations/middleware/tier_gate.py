"""Subscription tier gate decorator for Flask routes."""

from functools import wraps
from flask import request, jsonify, render_template
from flask_login import current_user


def require_tier(minimum_tier):
    """Decorator: block access if user's tier is below minimum.

    API requests (paths starting with /api/ or .svg): returns 403 JSON.
    HTML requests: renders upgrade_required.html.
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from ..services.subscription_service import get_user_tier, tier_at_least

            user_id = (
                getattr(current_user, "id", None)
                if current_user and current_user.is_authenticated
                else None
            )
            user_tier = get_user_tier(user_id)

            if tier_at_least(user_tier, minimum_tier):
                return f(*args, **kwargs)

            wants_json = request.path.startswith("/api/") or request.path.endswith(
                ".svg"
            )

            if wants_json:
                return (
                    jsonify(
                        {
                            "error": "upgrade_required",
                            "message": f"This feature requires {minimum_tier} tier or higher.",
                            "required_tier": minimum_tier,
                            "current_tier": user_tier,
                            "upgrade_url": "/settings#subscription",
                        }
                    ),
                    403,
                )

            return (
                render_template(
                    "upgrade_required.html",
                    required_tier=minimum_tier,
                    current_tier=user_tier,
                    feature=request.endpoint or "this feature",
                    tier_prices={"pro": 29, "business": 49, "enterprise": 99},
                ),
                403,
            )

        return decorated_function

    return decorator
