"""Authentication for standalone deployment."""
import os
import bcrypt
from flask import request, redirect, render_template, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from .db import get_db

VALID_THEMES = ("kytran", "lcars", "midnight", "arctic", "ember")
ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "svg", "webp"}

login_manager = LoginManager()


class User(UserMixin):
    def __init__(self, id, username, role):
        self.id = id
        self.username = username
        self.role = role
        self.is_admin = role == "admin"


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,)).fetchone()
    db.close()
    if row:
        return User(row["id"], row["username"], row["role"])
    return None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


def setup_required():
    """Check if first-run setup is needed."""
    try:
        db = get_db()
        count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        db.close()
        return count == 0
    except Exception:
        return True


def create_admin(username, password):
    """Create the initial admin user."""
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db = get_db()
    db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
               (username, pw_hash))
    db.commit()
    db.close()


def verify_password(username, password):
    """Verify username/password, return User or None."""
    db = get_db()
    row = db.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?",
                     (username,)).fetchone()
    db.close()
    if row and bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return User(row["id"], row["username"], row["role"])
    return None


def register_auth_routes(app):
    """Register login/logout/setup routes."""
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = verify_password(request.form["username"], request.form["password"])
            if user:
                login_user(user)
                return redirect(request.args.get("next", "/"))
            flash("Invalid credentials", "error")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect("/login")

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if not setup_required():
            return redirect("/")
        if request.method == "POST":
            create_admin(request.form["username"], request.form["password"])
            flash("Admin account created. Please log in.", "success")
            return redirect("/login")
        return render_template("setup.html")

    @app.route("/settings")
    @login_required
    def settings():
        current_theme = os.environ.get("SYSOPS_THEME", "kytran")
        return render_template("settings.html", current_theme=current_theme)

    @app.route("/settings/theme", methods=["POST"])
    @login_required
    def set_theme():
        theme = request.json.get("theme", "kytran")
        if theme not in VALID_THEMES:
            return jsonify({"success": False, "error": "Invalid theme"}), 400

        # Update the theme and regenerate CSS
        os.environ["SYSOPS_THEME"] = theme
        from .theme import load_theme, write_theme_css
        theme_data = load_theme(theme)
        css_path = os.path.join(app.static_folder, "css", "system-operations-theme-vars.css")
        write_theme_css(theme_data, css_path)

        # Refresh the context processor with new theme
        app.jinja_env.globals["sysops_theme"] = theme_data

        # Store preference in DB
        db = get_db()
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('theme', ?)", (theme,))
        db.commit()
        db.close()

        return jsonify({"success": True, "theme": theme})

    @app.route("/settings/password", methods=["POST"])
    @login_required
    def change_password():
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")

        if len(new_pw) < 8:
            flash("New password must be at least 8 characters", "error")
            return redirect("/settings")

        db = get_db()
        row = db.execute("SELECT password_hash FROM users WHERE id = ?", (current_user.id,)).fetchone()
        if not row or not bcrypt.checkpw(current_pw.encode(), row["password_hash"].encode()):
            db.close()
            flash("Current password is incorrect", "error")
            return redirect("/settings")

        new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, current_user.id))
        db.commit()
        db.close()
        flash("Password updated successfully", "success")
        return redirect("/settings")

    @app.route("/settings/logo", methods=["POST"])
    @login_required
    def upload_logo():
        if "logo" not in request.files:
            return jsonify({"success": False, "error": "No file provided"}), 400
        file = request.files["logo"]
        if not file.filename:
            return jsonify({"success": False, "error": "No file selected"}), 400
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in ALLOWED_LOGO_EXTENSIONS:
            return jsonify({"success": False, "error": f"Invalid file type. Allowed: {', '.join(ALLOWED_LOGO_EXTENSIONS)}"}), 400
        filename = secure_filename(f"custom-logo.{ext}")
        logo_dir = os.path.join(app.static_folder, "img")
        os.makedirs(logo_dir, exist_ok=True)
        filepath = os.path.join(logo_dir, filename)
        file.save(filepath)
        logo_url = f"/static/img/{filename}"

        # Store in DB
        db = get_db()
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('custom_logo', ?)", (logo_url,))
        db.commit()
        db.close()

        # Update theme context
        if "sysops_theme" in app.jinja_env.globals:
            app.jinja_env.globals["sysops_theme"]["logo"] = logo_url

        return jsonify({"success": True, "logo_url": logo_url})

    @app.route("/auth/sso", methods=["GET", "POST"])
    def sso_login():
        """SSO endpoint stub -- ready for Tier 2 'Sign in with Kytran' OAuth flow."""
        return jsonify({
            "error": "SSO not yet configured",
            "message": "Sign in with Kytran will be available in a future update. Use local login.",
            "tier": 2,
            "docs": "See standalone-extraction.md Tier 2 requirements",
        }), 501

    @app.route("/auth/sso/status", methods=["GET"])
    def sso_status():
        """Check SSO configuration status."""
        return jsonify({
            "sso_enabled": False,
            "provider": "kytran",
            "auth_url": None,
            "message": "SSO pending Tier 2 implementation",
        })

    login_manager.login_view = "login"
