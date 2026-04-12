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
    admin_user = os.environ.get("KSM_ADMIN_USER")
    admin_pass = os.environ.get("KSM_ADMIN_PASSWORD")
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
        if req.endpoint and req.endpoint not in ("setup", "static", "top_health") and setup_required():
            return redirect("/setup")

    # Start background compliance scanner (skip in testing)
    if not app.config.get("TESTING"):
        from .services.scheduler import start_scheduler
        start_scheduler(app)

    return app


def main():
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)


if __name__ == "__main__":
    main()
