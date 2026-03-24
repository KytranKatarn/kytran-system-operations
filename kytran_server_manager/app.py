"""Kytran Server Manager — Standalone Flask Application."""
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

    # Initialize SQLite
    init_db(app.config.get("DB_PATH", Config.DB_PATH))

    # Initialize auth
    login_manager.init_app(app)
    register_auth_routes(app)

    # Initialize theme
    os.environ["SYSOPS_THEME"] = app.config.get("THEME", "kytran")
    init_theme(app)

    # Register routes
    from .routes import register_all_routes
    register_all_routes(app, admin_required)

    @app.route("/")
    @login_required
    def index():
        if setup_required():
            return redirect("/setup")
        return redirect("/dashboard")

    return app


def main():
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)


if __name__ == "__main__":
    main()
