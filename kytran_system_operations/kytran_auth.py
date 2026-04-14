"""Kytran Auth SDK — "Sign in with Kytran" OAuth client for Flask apps.

Usage:
    from kytran_auth import KytranAuth

    kytran_auth = KytranAuth()
    kytran_auth.init_app(app)

Env vars required:
    KYTRAN_CLIENT_ID       — OAuth client ID (e.g., "kso")
    KYTRAN_CLIENT_SECRET   — OAuth client secret
    KYTRAN_AUTH_URL        — ARCHIE hub URL (e.g., "https://archie.example.com")
    KYTRAN_REDIRECT_URI    — Callback URL for this product
"""

import os
import secrets
from functools import wraps

import requests
from flask import redirect, request, session, url_for, jsonify


class KytranAuth:
    def __init__(self, app=None):
        self.client_id = None
        self.client_secret = None
        self.auth_url = None
        self.redirect_uri = None
        self.app = None
        self._on_login = None
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Configure OAuth settings and register auth routes."""
        self.app = app
        self.client_id = app.config.get("KYTRAN_CLIENT_ID") or os.environ.get("KYTRAN_CLIENT_ID", "")
        self.client_secret = app.config.get("KYTRAN_CLIENT_SECRET") or os.environ.get("KYTRAN_CLIENT_SECRET", "")
        self.auth_url = app.config.get("KYTRAN_AUTH_URL") or os.environ.get("KYTRAN_AUTH_URL", "")
        self.redirect_uri = app.config.get("KYTRAN_REDIRECT_URI") or os.environ.get("KYTRAN_REDIRECT_URI", "")

        self._register_routes(app)

    def _register_routes(self, app):
        """Register /auth/kytran/login and /auth/kytran/callback routes."""

        sdk = self

        @app.route("/auth/kytran/login")
        def kytran_login():
            state = secrets.token_urlsafe(32)
            session["oauth_state"] = state
            session["oauth_next"] = request.args.get("next", "/")

            authorize_url = (
                f"{sdk.auth_url}/oauth/authorize"
                f"?client_id={sdk.client_id}"
                f"&redirect_uri={sdk.redirect_uri}"
                f"&response_type=code"
                f"&state={state}"
            )
            return redirect(authorize_url)

        @app.route("/auth/kytran/callback")
        def kytran_callback():
            state = request.args.get("state")
            if state != session.pop("oauth_state", None):
                return "Invalid state parameter", 400

            code = request.args.get("code")
            if not code:
                error = request.args.get("error", "unknown")
                return f"Authorization failed: {error}", 400

            try:
                token_resp = requests.post(
                    f"{sdk.auth_url}/oauth/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_id": sdk.client_id,
                        "client_secret": sdk.client_secret,
                        "redirect_uri": sdk.redirect_uri,
                    },
                    timeout=10,
                )
                if token_resp.status_code != 200:
                    return f"Token exchange failed: {token_resp.text}", 400
                token_data = token_resp.json()
            except Exception as e:
                return f"Token exchange error: {e}", 500

            access_token = token_data.get("access_token")
            if not access_token:
                return "No access token received", 400

            try:
                userinfo_resp = requests.get(
                    f"{sdk.auth_url}/oauth/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if userinfo_resp.status_code != 200:
                    return f"Userinfo failed: {userinfo_resp.text}", 400
                userinfo = userinfo_resp.json()
            except Exception as e:
                return f"Userinfo error: {e}", 500

            entitlements = userinfo.get("entitlements", [])
            if sdk.client_id not in entitlements:
                return (
                    "<h2>Subscription Required</h2>"
                    "<p>Your account does not have access to this product.</p>"
                    f"<p>Subscribed products: {', '.join(entitlements) or 'none'}</p>"
                    f"<p><a href='{sdk.auth_url}'>Manage subscriptions</a></p>"
                ), 403

            session["kytran_user"] = {
                "sub": userinfo.get("sub"),
                "username": userinfo.get("username"),
                "email": userinfo.get("email"),
                "name": userinfo.get("name"),
                "role": userinfo.get("role"),
                "entitlements": entitlements,
                "product_tiers": userinfo.get("product_tiers", {}),
                "access_token": access_token,
            }

            if sdk._on_login:
                sdk._on_login(session["kytran_user"])

            next_url = session.pop("oauth_next", "/")
            return redirect(next_url)

    def on_login(self, callback):
        """Register a callback for when SSO login succeeds."""
        self._on_login = callback
        return callback

    def login_required(self, f):
        """Decorator: check session for Kytran SSO user, redirect if not found."""
        @wraps(f)
        def decorated(*args, **kwargs):
            if "kytran_user" not in session:
                return redirect(url_for("kytran_login", next=request.url))
            return f(*args, **kwargs)
        return decorated

    def get_current_user(self):
        """Return current Kytran user from session, or None."""
        return session.get("kytran_user")

    def has_entitlement(self, entitlement):
        """Check if current user has a specific product entitlement."""
        user = self.get_current_user()
        if not user:
            return False
        return entitlement in user.get("entitlements", [])
