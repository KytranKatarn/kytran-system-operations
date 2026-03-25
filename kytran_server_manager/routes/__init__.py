"""Route registration for standalone app."""
from flask import Blueprint


def register_all_routes(app, admin_required_decorator):
    bp = Blueprint("sysops", __name__, url_prefix="/dashboard")

    from .core_routes import register_core_routes
    from .process_routes import register_process_routes
    from .docker_routes import register_docker_routes
    from .stack_routes import register_stack_routes
    from .storage_routes import register_storage_routes
    from .network_routes import register_network_routes
    from .firewall_routes import register_firewall_routes
    from .health_routes import register_health_routes
    from .compliance_routes import register_compliance_routes
    from .proxy_routes import register_proxy_routes

    register_core_routes(bp, admin_required_decorator)
    register_process_routes(bp, admin_required_decorator)
    register_docker_routes(bp, admin_required_decorator)
    register_stack_routes(bp, admin_required_decorator)
    register_storage_routes(bp, admin_required_decorator)
    register_network_routes(bp, admin_required_decorator)
    register_firewall_routes(bp, admin_required_decorator)
    register_health_routes(bp, admin_required_decorator)
    register_compliance_routes(bp, admin_required_decorator)
    register_proxy_routes(bp, admin_required_decorator)

    app.register_blueprint(bp)
