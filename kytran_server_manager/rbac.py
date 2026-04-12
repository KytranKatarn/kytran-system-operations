"""
RBAC shim for Kytran System Operations standalone.

Mirrors platform_v2/rbac.py contract so copied platform modules can
import `require_permission` and `Permission` without rewrites.

In the standalone there's only one permission tier: admin. Every
permission check routes through `admin_required` from auth.py.
"""

from enum import Enum

try:
    from .auth import admin_required
except ImportError:
    # When imported via sys.path as a top-level module, relative imports fail.
    from kytran_server_manager.auth import admin_required


class Permission(Enum):
    """Single-tier permission model for the standalone."""

    ADMIN_ACCESS = "admin_access"
    SYSTEM_READ = "admin_access"
    SYSTEM_WRITE = "admin_access"
    MANAGE_USERS = "admin_access"
    VIEW_AUDIT = "admin_access"


def require_permission(permission):
    """Platform-compatible wrapper. In standalone, all perms map to admin_required."""
    return admin_required
