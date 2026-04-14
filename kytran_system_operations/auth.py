"""Authentication for standalone deployment."""
import logging
import os
import bcrypt
from urllib.parse import urlparse
from flask import request, redirect, render_template, flash, jsonify, url_for
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from .db import get_db
from .services.hub_client import is_hub_configured, exchange_code_for_token
from .kytran_auth import KytranAuth

logger = logging.getLogger(__name__)

VALID_THEMES = ("kytran", "lcars", "midnight", "arctic", "ember")
ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "svg", "webp"}

login_manager = LoginManager()
kytran_auth = KytranAuth()


class User(UserMixin):
    def __init__(self, id, username, role, display_name=None, email=None, sso_provider=None):
        self.id = id
        self.username = username
        self.role = role
        self.is_admin = role == "admin"
        self.display_name = display_name or username
        self.email = email
        self.sso_provider = sso_provider
        self.is_sso = sso_provider is not None and sso_provider != ""
        self.is_local = not self.is_sso


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT id, username, role, display_name, email, sso_provider FROM users WHERE id = ?", (user_id,)).fetchone()
    db.close()
    if row:
        return User(row["id"], row["username"], row["role"], row["display_name"], row["email"], row["sso_provider"])
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
    row = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    db.close()
    if row:
        from .services.subscription_service import set_user_tier
        set_user_tier(row["id"], "pro")


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

    @app.route("/")
    def splash():
        if current_user.is_authenticated:
            return redirect(url_for("system_operations.index"))
        return render_template("landing.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = verify_password(request.form["username"], request.form["password"])
            if user:
                login_user(user)
                if user.is_admin:
                    from .services.subscription_service import get_user_tier, set_user_tier
                    if get_user_tier(user.id) == "free":
                        set_user_tier(user.id, "pro")
                next_url = request.args.get("next", "/")
                if urlparse(next_url).netloc:
                    next_url = "/"
                return redirect(next_url)
            flash("Invalid credentials", "error")
        sso_enabled = is_hub_configured()
        return render_template("login.html", sso_enabled=sso_enabled)

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
        from .services.subscription_service import get_user_tier, get_allowed_themes, TIER_LEVELS

        current_theme = os.environ.get("SYSOPS_THEME", "kytran")
        tier = get_user_tier(current_user.id)
        max_themes = get_allowed_themes(tier)
        all_themes = list(VALID_THEMES)  # ordered list
        allowed_themes = all_themes[:max_themes]

        return render_template(
            "settings.html",
            current_theme=current_theme,
            tier=tier,
            allowed_themes=allowed_themes,
            all_themes=all_themes,
        )

    @app.route("/settings/theme", methods=["POST"])
    @login_required
    def set_theme():
        from .services.subscription_service import get_user_tier, get_allowed_themes

        theme = request.json.get("theme", "kytran")
        if theme not in VALID_THEMES:
            return jsonify({"success": False, "error": "Invalid theme"}), 400

        # Tier-gate: check if the user's subscription allows this theme
        tier = get_user_tier(current_user.id)
        max_themes = get_allowed_themes(tier)
        allowed_themes = list(VALID_THEMES)[:max_themes]
        if theme not in allowed_themes:
            return jsonify({"success": False, "error": "Theme requires a higher subscription tier"}), 403

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

    @app.route("/settings/profile", methods=["POST"])
    @login_required
    def update_profile():
        display_name = request.form.get("display_name", "").strip()
        email = request.form.get("email", "").strip()
        db = get_db()
        db.execute("UPDATE users SET display_name = ?, email = ? WHERE id = ?",
                   (display_name or None, email or None, current_user.id))
        db.commit()
        db.close()
        flash("Profile updated", "success")
        return redirect("/settings")

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

    @app.route("/auth/sso", methods=["GET"])
    def sso_login():
        """Redirect to ARCHIE OAuth authorize endpoint."""
        if not is_hub_configured():
            return jsonify({
                "error": "SSO not yet configured",
                "message": "Connect with Kytran Empowerment requires hub connection. Use local login.",
            }), 501

        # SSO requires pro tier (only check if already logged in)
        if current_user and current_user.is_authenticated:
            from .services.subscription_service import get_user_tier, tier_at_least
            tier = get_user_tier(current_user.id)
            if not tier_at_least(tier, "pro"):
                return render_template("upgrade_required.html",
                    required_tier="pro", current_tier=tier,
                    feature="Connect with Kytran Empowerment (SSO)",
                    tier_prices={"pro": 29, "business": 49, "enterprise": 99}), 403

        hub_url = app.config["ARCHIE_HUB_URL"].rstrip("/")
        client_id = app.config.get("ARCHIE_CLIENT_ID", "kytran-sysops")
        callback_url = request.url_root.rstrip("/") + "/auth/sso/callback"

        authorize_url = (
            f"{hub_url}/oauth/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={callback_url}"
            f"&response_type=code"
        )
        return redirect(authorize_url)

    @app.route("/auth/sso/callback", methods=["GET"])
    def sso_callback():
        """Handle OAuth callback from ARCHIE hub."""
        code = request.args.get("code")
        error = request.args.get("error")

        if error:
            logger.warning("SSO callback received error: %s", error)
            flash("SSO authentication was denied or failed.", "error")
            return redirect(url_for("login"))

        if not code:
            flash("SSO callback missing authorization code.", "error")
            return redirect(url_for("login"))

        callback_url = request.url_root.rstrip("/") + "/auth/sso/callback"

        try:
            token_data = exchange_code_for_token(code, callback_url)
        except Exception:
            logger.exception("SSO token exchange failed")
            flash("SSO authentication failed. Please try again.", "error")
            return redirect(url_for("login"))

        if not token_data:
            flash("SSO authentication failed. Could not obtain token.", "error")
            return redirect(url_for("login"))

        # Extract user info from token response
        username = token_data.get("username") or token_data.get("user", {}).get("username")
        if not username:
            flash("SSO response missing user information.", "error")
            return redirect(url_for("login"))

        # Find or create local user for this SSO identity
        db = get_db()
        row = db.execute(
            "SELECT id, username, role FROM users WHERE username = ?", (username,)
        ).fetchone()

        if row:
            user = User(row["id"], row["username"], row["role"])
        else:
            # Create new user from SSO (empty password_hash — authenticates via ARCHIE)
            db.execute(
                "INSERT INTO users (username, password_hash, role, sso_provider) VALUES (?, '', 'user', 'archie')",
                (username,),
            )
            db.commit()
            new_row = db.execute(
                "SELECT id, username, role FROM users WHERE username = ?", (username,)
            ).fetchone()
            user = User(new_row["id"], new_row["username"], new_row["role"])

        db.close()
        login_user(user)
        logger.info("SSO login successful for user: %s", username)
        return redirect("/")

    @app.route("/auth/sso/status", methods=["GET"])
    def sso_status():
        """Check SSO configuration status."""
        enabled = is_hub_configured()
        return jsonify({
            "sso_enabled": enabled,
            "provider": "kytran",
            "auth_url": "/auth/sso" if enabled else None,
        })

    # --- Kytran Auth SDK (Sign in with Kytran via OAuth) ---
    kytran_auth.init_app(app)

    @kytran_auth.on_login
    def handle_kytran_login(userinfo):
        """Create or update local user from Kytran SSO login."""
        import secrets as secrets_mod
        from werkzeug.security import generate_password_hash

        db = get_db()
        username = userinfo.get("username", "")

        row = db.execute(
            "SELECT id, username, role FROM users WHERE username = ? AND sso_provider = 'kytran'",
            (username,),
        ).fetchone()

        if row:
            db.execute(
                "UPDATE users SET role = ? WHERE id = ?",
                (userinfo.get("role", "admin"), row["id"]),
            )
            db.commit()
            user = User(row["id"], row["username"], userinfo.get("role", "admin"))
        else:
            db.execute(
                "INSERT INTO users (username, password_hash, role, sso_provider) VALUES (?, ?, ?, 'kytran')",
                (username, generate_password_hash(secrets_mod.token_urlsafe(32)), userinfo.get("role", "admin")),
            )
            db.commit()
            new_row = db.execute(
                "SELECT id, username, role FROM users WHERE username = ? AND sso_provider = 'kytran'",
                (username,),
            ).fetchone()
            user = User(new_row["id"], new_row["username"], new_row["role"])

        db.close()
        login_user(user)

        # Sync subscription tier from ARCHIE's product_tiers
        product_tiers = userinfo.get("product_tiers", {})
        kso_tier = product_tiers.get("kso", "free")
        try:
            from .services.subscription_service import set_user_tier
            set_user_tier(user.id, kso_tier)
            logger.info("Synced tier '%s' for user %s from ARCHIE", kso_tier, username)
        except Exception as e:
            logger.warning("Failed to sync tier for %s: %s", username, e)

        logger.info("Kytran SSO login successful for user: %s", username)

    login_manager.login_view = "login"
