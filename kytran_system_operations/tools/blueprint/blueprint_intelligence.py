"""
Blueprint Intelligence stub for standalone.

The ARCHIE platform has a Blueprint Intelligence module that provides
cross-module system health aggregation. The standalone doesn't ship it.
This stub returns a minimal health structure so `health_routes.py` can
import the function without crashing.

If richer health data is needed in the standalone, implement it here
against the standalone's own db.
"""


def get_system_health():
    """Return a minimal system health snapshot.

    Returns:
        dict: {
            "overall": "healthy" | "degraded" | "down",
            "subsystems": {name: status, ...},
            "message": str,
        }
    """
    return {
        "overall": "healthy",
        "subsystems": {},
        "message": "Blueprint Intelligence not enabled in standalone build",
    }
