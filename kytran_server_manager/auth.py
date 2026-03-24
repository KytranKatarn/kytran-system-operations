"""Authentication for standalone deployment."""
import bcrypt
from flask import request, redirect, render_template, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps
from .db import get_db

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

    login_manager.login_view = "login"
