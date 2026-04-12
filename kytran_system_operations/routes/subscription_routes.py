"""Subscription routes for KSM feature gating."""
from flask import jsonify, request
from flask_login import current_user, login_required


def register_subscription_routes(bp, admin_required):
    from ..services.subscription_service import (
        TIER_LEVELS,
        get_allowed_packs,
        get_allowed_themes,
        get_scan_interval,
        get_user_tier,
        set_user_tier,
    )

    @bp.route("/api/subscription/status", methods=["GET"])
    @login_required
    def subscription_status():
        tier = get_user_tier(current_user.id)
        return jsonify(
            {
                "tier": tier,
                "allowed_packs": get_allowed_packs(tier),
                "allowed_themes": get_allowed_themes(tier),
                "scan_interval": get_scan_interval(tier),
                "tiers": TIER_LEVELS,
            }
        )

    @bp.route("/api/admin/subscription/<int:user_id>", methods=["PUT"])
    @login_required
    @admin_required
    def admin_set_subscription(user_id):
        data = request.get_json(silent=True) or {}
        tier = data.get("tier")
        if not tier or tier not in TIER_LEVELS:
            return jsonify({"error": f"Invalid tier. Must be one of: {list(TIER_LEVELS.keys())}"}), 400
        set_user_tier(user_id, tier)
        return jsonify({"ok": True, "user_id": user_id, "tier": tier})
