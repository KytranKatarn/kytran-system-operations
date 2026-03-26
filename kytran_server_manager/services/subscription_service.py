"""Subscription service — tier resolution and feature gating for KSM."""

from flask import current_app

from db import get_db

# ---------------------------------------------------------------------------
# Tier hierarchy
# ---------------------------------------------------------------------------
TIER_LEVELS = {"free": 0, "pro": 1, "business": 2, "enterprise": 3}

# ---------------------------------------------------------------------------
# Feature gates per tier
# ---------------------------------------------------------------------------
TIER_PACKS = {
    "free": ["cis-ubuntu"],
    "pro": ["cis-ubuntu", "ubuntu-stig", "network-stig"],
    "business": ["cis-ubuntu", "ubuntu-stig", "network-stig", "docker-bench", "pci-dss"],
    "enterprise": ["cis-ubuntu", "ubuntu-stig", "network-stig", "docker-bench", "pci-dss"],
}

TIER_THEMES = {
    "free": 2,
    "pro": 3,
    "business": 5,
    "enterprise": 5,
}

TIER_SCAN_INTERVAL = {
    "free": 86400,       # 24 h
    "pro": 21600,        # 6 h
    "business": 3600,    # 1 h
    "enterprise": None,  # from config
}


# ---------------------------------------------------------------------------
# Tier resolution
# ---------------------------------------------------------------------------
def get_user_tier(user_id=None):
    """Return the effective tier string for a user.

    Priority: config override > DB subscription > default 'free'.
    """
    # 1. Global override (useful for dev/demo)
    override = current_app.config.get("TIER_OVERRIDE")
    if override and override in TIER_LEVELS:
        return override

    # 2. DB lookup
    if user_id is not None:
        db = get_db()
        try:
            row = db.execute(
                "SELECT tier FROM subscriptions WHERE user_id = ? AND status = 'active'",
                (user_id,),
            ).fetchone()
            if row:
                return row["tier"]
        finally:
            db.close()

    # 3. Default
    return "free"


def tier_at_least(user_tier, minimum_tier):
    """Return True when *user_tier* meets or exceeds *minimum_tier*."""
    return TIER_LEVELS.get(user_tier, 0) >= TIER_LEVELS.get(minimum_tier, 0)


# ---------------------------------------------------------------------------
# Feature accessors
# ---------------------------------------------------------------------------
def get_allowed_packs(tier):
    """Return list of compliance-pack IDs available at *tier*."""
    return list(TIER_PACKS.get(tier, TIER_PACKS["free"]))


def get_allowed_themes(tier):
    """Return max number of themes available at *tier*."""
    return TIER_THEMES.get(tier, TIER_THEMES["free"])


def get_scan_interval(tier):
    """Return minimum scan interval (seconds) for *tier*.

    Enterprise reads from ``COMPLIANCE_SCAN_INTERVAL`` config; falls back to
    3600 if unset.
    """
    interval = TIER_SCAN_INTERVAL.get(tier)
    if interval is None:
        # Enterprise — defer to config
        return current_app.config.get("COMPLIANCE_SCAN_INTERVAL", 3600)
    return interval


# ---------------------------------------------------------------------------
# Admin setter
# ---------------------------------------------------------------------------
def set_user_tier(user_id, tier):
    """Create or update the subscription tier for *user_id* (UPSERT)."""
    if tier not in TIER_LEVELS:
        raise ValueError(f"Invalid tier: {tier!r}")

    db = get_db()
    try:
        db.execute(
            """INSERT INTO subscriptions (user_id, tier, status, updated_at)
               VALUES (?, ?, 'active', datetime('now'))
               ON CONFLICT(user_id) DO UPDATE
               SET tier = excluded.tier,
                   updated_at = excluded.updated_at""",
            (user_id, tier),
        )
        db.commit()
    finally:
        db.close()
