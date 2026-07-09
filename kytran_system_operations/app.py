"""Kytran System Operations — Standalone Flask Application."""
import os
from flask import Flask, redirect
from flask_login import login_required
from .config import Config
from .db import init_db
from .auth import login_manager, admin_required, register_auth_routes, setup_required
from .theme import init_theme


def create_app(config=None):
    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(__file__), "templates"),
                static_folder=os.path.join(os.path.dirname(__file__), "static"))

    app.config.from_object(config or Config)
    os.makedirs(app.config.get("DATA_DIR", Config.DATA_DIR), exist_ok=True)

    # Initialize SQLite (native standalone: users, api_keys, audit, settings)
    init_db(app.config.get("DB_PATH", Config.DB_PATH))

    # Initialize Postgres sidecar (for modules copied from ARCHIE platform).
    # Graceful fallback: if sidecar is unreachable, log and continue so the
    # app can still serve auth + landing + compliance scanner features that
    # don't depend on platform module tables.
    if os.environ.get("DB_HOST"):
        from .migrations import run_migrations
        import time
        for attempt in range(3):
            try:
                run_migrations()
                app.logger.info("Postgres schema migrations applied")
                break
            except Exception as e:
                if attempt < 2:
                    app.logger.warning("Postgres not ready (attempt %d/3): %s", attempt + 1, e)
                    time.sleep(2)
                else:
                    app.logger.warning(
                        "Postgres sidecar unavailable — platform module features degraded: %s", e
                    )

    # Initialize auth
    login_manager.init_app(app)
    register_auth_routes(app)

    # Auto-seed admin from env vars (skip manual setup for host owner)
    admin_user = os.environ.get("KSO_ADMIN_USER")
    admin_pass = os.environ.get("KSO_ADMIN_PASSWORD")
    if admin_user and admin_pass and setup_required():
        from .auth import create_admin
        create_admin(admin_user, admin_pass)
        app.logger.info("Auto-created admin account from env vars: %s", admin_user)

    # Load saved theme preference from DB, fall back to config
    saved_theme = app.config.get("THEME", "kytran")
    try:
        from .db import get_db
        db = get_db()
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        row = db.execute("SELECT value FROM settings WHERE key = 'theme'").fetchone()
        if row:
            saved_theme = row["value"]
        db.close()
    except Exception:
        pass
    os.environ["SYSOPS_THEME"] = saved_theme
    init_theme(app)

    # Load custom logo from DB if set
    try:
        db2 = get_db()
        logo_row = db2.execute("SELECT value FROM settings WHERE key = 'custom_logo'").fetchone()
        if logo_row and logo_row["value"]:
            app.jinja_env.globals["sysops_theme"]["logo"] = logo_row["value"]
        db2.close()
    except Exception:
        pass

    # Load compliance rule packs
    try:
        from .services.compliance_service import load_all_packs
        loaded = load_all_packs()
        if loaded:
            app.logger.info("Loaded %d compliance rule packs", len(loaded))
    except Exception as e:
        app.logger.warning("Compliance packs not loaded: %s", e)

    # Register routes
    from .routes import register_all_routes
    register_all_routes(app, admin_required)

    @app.route("/health")
    def top_health():
        """Top-level health check (no auth, no setup redirect)."""
        return {"healthy": True, "service": "kytran-system-operations"}

    @app.route("/version")
    def version():
        """Deploy-provenance (#4776) — build metadata only. KSO has no git repo,
        so git_sha stays 'unknown'; build_time reflects the last image build.
        Manual endpoint (not drift-monitored, no repo to compare against)."""
        return {
            "product": "kso",
            "git_sha": os.environ.get("KSO_GIT_SHA", "unknown"),
            "build_time": os.environ.get("KSO_BUILD_TIME", "unknown"),
        }

    @app.route("/")
    def index():
        if setup_required():
            return redirect("/setup")
        from flask_login import current_user
        if not current_user.is_authenticated:
            return redirect("/login")
        return redirect("/dashboard")

    @app.before_request
    def check_setup():
        """Redirect ALL requests to /setup if no admin account exists."""
        from flask import request as req
        excluded = ("setup", "static", "top_health", "version", "login", "logout", "splash",
                    "sso_login", "sso_callback", "sso_status", "kytran_login", "kytran_callback")
        if req.endpoint and req.endpoint not in excluded and setup_required():
            return redirect("/setup")

    @app.before_request
    def check_subscription_gate():
        """Block non-Professional users from all routes except auth/static."""
        from flask import request as req, jsonify, render_template
        from flask_login import current_user

        exempt = {
            "setup", "static", "top_health", "version",
            "login", "logout", "splash",
            "sso_login", "sso_callback", "sso_status",
            "kytran_login", "kytran_callback",
        }
        if req.endpoint is None or req.endpoint in exempt:
            return None
        if not current_user.is_authenticated:
            return None  # login_required handles the redirect

        from .services.subscription_service import get_user_tier, tier_at_least
        tier = get_user_tier(current_user.id)
        if tier_at_least(tier, "pro"):
            return None

        # Free-tier user — show upgrade wall
        if req.path.startswith("/api/") or req.path.endswith(".svg"):
            return jsonify({
                "error": "upgrade_required",
                "message": "Kytran System Operations requires a Professional subscription.",
                "required_tier": "pro",
                "current_tier": tier,
                "upgrade_url": "https://business.kytranempowerment.com/billing/dashboard",
            }), 403

        return render_template(
            "upgrade_required.html",
            required_tier="pro",
            current_tier=tier,
            feature="Kytran System Operations",
            tier_prices={"pro": 29, "business": 49, "enterprise": 99},
        ), 403

    # Start background compliance scanner (skip in testing)
    if not app.config.get("TESTING"):
        from .services.scheduler import start_scheduler
        start_scheduler(app)

        # Start background metrics collector — samples CPU/memory/GPU every 5 min
        # so graph history persists between page loads instead of resetting to now
        from .services.metrics_collector import start_collector
        start_collector(app)

    # App-level stubs for lcars-header.js polling calls (no blueprint prefix)
    # These endpoints are called from the shared LCARS header JS which was
    # inherited from the platform — KSO has no platform alerts/DHQ, return empty.
    from flask import jsonify as _jsonify
    from flask_login import login_required as _lr

    @app.route("/api/alerts/unified")
    @_lr
    def _alerts_unified():
        return _jsonify({"success": True, "counts": {"unread": 0, "critical": 0, "total": 0}, "alerts": []})

    @app.route("/api/alerts/unified/mark-read", methods=["POST"])
    @_lr
    def _alerts_mark_read():
        return _jsonify({"success": True})

    @app.route("/api/alerts/unified/mark-all-read", methods=["POST"])
    @_lr
    def _alerts_mark_all_read():
        return _jsonify({"success": True})

    @app.route("/tools/department-hq/api/dispatch/ops-summary")
    @_lr
    def _ops_summary_stub():
        return _jsonify({"approvals": {"pending": 0}, "active_jobs": {}, "errors_1h": {}})

    return app


def main():
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)


if __name__ == "__main__":
    main()
