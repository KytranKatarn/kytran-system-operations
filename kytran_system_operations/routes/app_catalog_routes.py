"""App Catalog routes — browse, deploy, manage fleet apps."""

from flask import jsonify, render_template, request
from flask_login import login_required

from ..services.app_catalog_service import (
    check_app_health,
    deploy_app,
    get_app_logs,
    get_catalog,
    get_installed_apps,
    remove_app,
    start_app,
    stop_app,
    update_app,
)


def register_app_catalog_routes(bp, admin_required_decorator):
    """Register app catalog routes on the sysops blueprint."""

    @bp.route("/apps")
    @login_required
    @admin_required_decorator
    def apps_page():
        return render_template("apps.html")

    @bp.route("/api/apps/catalog", methods=["GET"])
    @login_required
    @admin_required_decorator
    def api_catalog():
        force = request.args.get("refresh") == "true"
        apps = get_catalog(force_refresh=force)
        installed = {a["id"]: a for a in get_installed_apps()}

        for app in apps:
            inst = installed.get(app["id"])
            if inst:
                app["installed"] = True
                app["status"] = inst["status"]
                app["installed_at"] = inst.get("installed_at")
            else:
                app["installed"] = False
                app["status"] = "available"

        return jsonify({"success": True, "apps": apps, "count": len(apps)})

    @bp.route("/api/apps/installed", methods=["GET"])
    @login_required
    @admin_required_decorator
    def api_installed():
        apps = get_installed_apps()
        return jsonify({"success": True, "apps": apps, "count": len(apps)})

    @bp.route("/api/apps/<app_id>/deploy", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_deploy(app_id):
        catalog = get_catalog()
        entry = next((a for a in catalog if a.get("id") == app_id), None)
        if not entry:
            return jsonify({"success": False, "error": "App not found in catalog"}), 404

        env_overrides = (request.get_json() or {}).get("env", {})
        success, message = deploy_app(app_id, entry, env_overrides)
        return jsonify({"success": success, "message": message}), 200 if success else 500

    @bp.route("/api/apps/<app_id>/stop", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_stop(app_id):
        success, message = stop_app(app_id)
        return jsonify({"success": success, "message": message}), 200 if success else 500

    @bp.route("/api/apps/<app_id>/start", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_start(app_id):
        success, message = start_app(app_id)
        return jsonify({"success": success, "message": message}), 200 if success else 500

    @bp.route("/api/apps/<app_id>/remove", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_remove(app_id):
        keep_data = (request.get_json() or {}).get("keep_data", False)
        success, message = remove_app(app_id, keep_data=keep_data)
        return jsonify({"success": success, "message": message}), 200 if success else 500

    @bp.route("/api/apps/<app_id>/update", methods=["POST"])
    @login_required
    @admin_required_decorator
    def api_update(app_id):
        catalog = get_catalog(force_refresh=True)
        entry = next((a for a in catalog if a.get("id") == app_id), None)
        if not entry:
            return jsonify({"success": False, "error": "App not found in catalog"}), 404
        success, message = update_app(app_id, entry)
        return jsonify({"success": success, "message": message}), 200 if success else 500

    @bp.route("/api/apps/<app_id>/logs", methods=["GET"])
    @login_required
    @admin_required_decorator
    def api_logs(app_id):
        lines = int(request.args.get("lines", 100))
        logs = get_app_logs(app_id, lines=lines)
        if logs is None:
            return jsonify({"success": False, "error": "App not installed"}), 404
        return jsonify({"success": True, "logs": logs})

    @bp.route("/api/apps/<app_id>/health", methods=["GET"])
    @login_required
    @admin_required_decorator
    def api_app_health(app_id):
        status = check_app_health(app_id)
        return jsonify({"success": True, "app_id": app_id, "health": status})
