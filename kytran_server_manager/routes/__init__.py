"""
Route registration for the standalone app.

The core_routes.py file was copied verbatim from ARCHIE's
platform_v2/tools/system_operations/routes.py. It uses the platform's
module-level blueprint pattern (`@system_operations_bp.route(...)`) with
`from . import system_operations_bp` at the top. We provide that
blueprint here so the copied file works byte-identically.

Sub-route modules (process, docker, stack, etc.) follow the ADR-045
injection pattern: `register_X_routes(bp, admin_required)`.
"""

from flask import Blueprint


# Blueprint exported for core_routes.py via `from . import system_operations_bp`.
# URL prefix matches the standalone's dashboard mount point.
system_operations_bp = Blueprint(
    "system_operations", __name__, url_prefix="/dashboard"
)


def register_all_routes(app, admin_required_decorator):
    """Wire every route module into the shared blueprint and register with app."""

    # Importing core_routes triggers its module-level @system_operations_bp.route
    # decorators. Must happen after the blueprint is created above.
    from . import core_routes  # noqa: F401

    # Sub-route modules from the copied platform (ADR-045 injection pattern)
    from .process_routes import register_process_routes
    from .docker_routes import register_docker_routes
    from .stack_routes import register_stack_routes
    from .storage_routes import register_storage_routes
    from .network_routes import register_network_routes
    from .firewall_routes import register_firewall_routes
    from .health_routes import register_health_routes
    from .proxy_routes import register_proxy_routes

    register_process_routes(system_operations_bp, admin_required_decorator)
    register_docker_routes(system_operations_bp, admin_required_decorator)
    register_stack_routes(system_operations_bp, admin_required_decorator)
    register_storage_routes(system_operations_bp, admin_required_decorator)
    register_network_routes(system_operations_bp, admin_required_decorator)
    register_firewall_routes(system_operations_bp, admin_required_decorator)
    register_health_routes(system_operations_bp, admin_required_decorator)
    register_proxy_routes(system_operations_bp, admin_required_decorator)

    # Standalone-only route modules (billing, compliance, SSO, app catalog)
    from .compliance_routes import register_compliance_routes
    from .badge_routes import register_badge_routes
    from .subscription_routes import register_subscription_routes
    from .billing_routes import register_billing_routes
    from .app_catalog_routes import register_app_catalog_routes

    register_compliance_routes(system_operations_bp, admin_required_decorator)
    register_badge_routes(system_operations_bp, admin_required_decorator)
    register_subscription_routes(system_operations_bp, admin_required_decorator)
    register_billing_routes(system_operations_bp, admin_required_decorator)
    register_app_catalog_routes(system_operations_bp, admin_required_decorator)

    app.register_blueprint(system_operations_bp)
