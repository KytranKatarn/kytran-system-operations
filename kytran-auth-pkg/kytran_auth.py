"""Kytran Auth SDK — "Sign in with Kytran" OAuth client for Flask apps.

Usage:
    from kytran_auth import KytranAuth

    kytran_auth = KytranAuth()
    kytran_auth.init_app(app)

Env vars required:
    KYTRAN_CLIENT_ID       — OAuth client ID (e.g., "kso")
    KYTRAN_CLIENT_SECRET   — OAuth client secret
    KYTRAN_AUTH_URL         — ARCHIE hub URL (e.g., "https://platform.kytranempowerment.com")
    KYTRAN_REDIRECT_URI    — Callback URL for this product
"""

import base64
import hashlib
import os
import secrets
import time
from functools import wraps

import requests
from flask import redirect, request, session, url_for, jsonify

# In-memory state store (survives across requests in same process)
# Format: {state_token: {"next": url, "expires": timestamp}}
_pending_states = {}


def _clean_expired_states():
    """Remove states older than 10 minutes."""
    now = time.time()
    expired = [k for k, v in _pending_states.items() if now - v["expires"] > 0]
    for k in expired:
        _pending_states.pop(k, None)


class KytranAuth:
    def __init__(self, app=None):
        self.client_id = None
        self.client_secret = None
        self.auth_url = None
        self.redirect_uri = None
        self.internal_url = None
        self.app = None
        self._on_login = None
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Configure OAuth settings and register auth routes."""
        self.app = app
        self.client_id = app.config.get("KYTRAN_CLIENT_ID") or os.environ.get("KYTRAN_CLIENT_ID", "")
        self.client_secret = app.config.get("KYTRAN_CLIENT_SECRET") or os.environ.get("KYTRAN_CLIENT_SECRET", "")
        self.auth_url = app.config.get("KYTRAN_AUTH_URL") or os.environ.get("KYTRAN_AUTH_URL", "https://platform.kytranempowerment.com")
        self.redirect_uri = app.config.get("KYTRAN_REDIRECT_URI") or os.environ.get("KYTRAN_REDIRECT_URI", "")
        self.internal_url = (
            app.config.get("KYTRAN_AUTH_INTERNAL_URL")
            or os.environ.get("KYTRAN_AUTH_INTERNAL_URL", "")
            or self.auth_url
        )

        self._register_routes(app)

    def _register_routes(self, app):
        """Register /auth/kytran/login and /auth/kytran/callback routes."""

        sdk = self

        @app.route("/auth/kytran/login")
        def kytran_login():
            # Already logged in AND session is complete? Skip OAuth.
            # NOTE: "kytran_user" alone is not enough — a previous failed _on_login
            # can leave it set without completing Flask-Login. Rely on Flask-Login.
            from flask_login import current_user as _cu
            if "kytran_user" in session and _cu.is_authenticated:
                next_url = request.args.get("next", "/maven/discover")
                return redirect(next_url)

            # Clear any stale kytran session left over from a previous partial auth
            session.pop("kytran_user", None)

            state = secrets.token_urlsafe(32)
            next_url = request.args.get("next", "/maven/discover")

            # PKCE — generate code_verifier and S256 code_challenge
            code_verifier = secrets.token_urlsafe(64)
            code_challenge = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(code_verifier.encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )

            # Store state in memory (not session — avoids cookie issues behind proxies)
            _clean_expired_states()
            _pending_states[state] = {
                "next": next_url,
                "expires": time.time() + 600,  # 10 min expiry
                "code_verifier": code_verifier,
            }

            # Also store in session as backup
            session["oauth_state"] = state
            session["oauth_next"] = next_url
            session["oauth_code_verifier"] = code_verifier

            authorize_url = (
                f"{sdk.auth_url}/oauth/authorize"
                f"?client_id={sdk.client_id}"
                f"&redirect_uri={sdk.redirect_uri}"
                f"&response_type=code"
                f"&state={state}"
                f"&code_challenge={code_challenge}"
                f"&code_challenge_method=S256"
            )
            return redirect(authorize_url)

        @app.route("/auth/kytran/callback")
        def kytran_callback():
            state = request.args.get("state")

            # Validate state: check in-memory store first, then session as fallback
            state_data = _pending_states.pop(state, None) if state else None
            session_state = session.pop("oauth_state", None)

            if not state_data and state != session_state:
                # Both checks failed — invalid state
                return (
                    "<h2>Session Expired</h2>"
                    "<p>Your login session expired. Please try again.</p>"
                    f'<p><a href="/auth/kytran/login?next=/maven/discover">Sign in with Kytran</a></p>'
                ), 400

            next_url = "/maven/discover"
            if state_data:
                next_url = state_data.get("next", "/maven/discover")
            else:
                next_url = session.pop("oauth_next", "/maven/discover")

            # Retrieve PKCE verifier (from state store, fall back to session)
            code_verifier = (
                state_data.get("code_verifier") if state_data
                else session.pop("oauth_code_verifier", None)
            )

            code = request.args.get("code")
            if not code:
                error = request.args.get("error", "unknown")
                return f"Authorization failed: {error}", 400

            # Exchange code for token
            token_payload = {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": sdk.client_id,
                "client_secret": sdk.client_secret,
                "redirect_uri": sdk.redirect_uri,
            }
            if code_verifier:
                token_payload["code_verifier"] = code_verifier

            try:
                token_resp = requests.post(
                    f"{sdk.internal_url}/oauth/token",
                    data=token_payload,
                    timeout=15,
                )
                if token_resp.status_code != 200:
                    return (
                        "<h2>Login Failed</h2>"
                        f"<p>Token exchange error. Please try again.</p>"
                        f'<p><a href="/auth/kytran/login?next=/maven/discover">Try again</a></p>'
                    ), 400
                token_data = token_resp.json()
                _refresh_token_val = token_data.get("refresh_token", "")
                _access_token_exp = int(time.time()) + token_data.get("expires_in", 28800)
            except Exception as e:
                return (
                    "<h2>Login Failed</h2>"
                    f"<p>Could not connect to auth server. Please try again.</p>"
                    f'<p><a href="/auth/kytran/login?next=/maven/discover">Try again</a></p>'
                ), 500

            access_token = token_data.get("access_token")
            if not access_token:
                return "No access token received", 400

            # Get user info
            try:
                userinfo_resp = requests.get(
                    f"{sdk.internal_url}/oauth/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15,
                )
                if userinfo_resp.status_code != 200:
                    return (
                        "<h2>Login Failed</h2>"
                        f"<p>Could not retrieve user info. Please try again.</p>"
                        f'<p><a href="/auth/kytran/login?next=/maven/discover">Try again</a></p>'
                    ), 400
                userinfo = userinfo_resp.json()
            except Exception as e:
                return f"Userinfo error: {e}", 500

            # Check entitlements
            entitlements = userinfo.get("entitlements", [])
            if sdk.client_id not in entitlements:
                # Check if any entitlement starts with the base product name
                base = sdk.client_id.replace("-viewer", "").replace("-pro", "").replace("-ai", "")
                has_access = any(e.startswith(base) for e in entitlements)
                if not has_access and userinfo.get("role") != "admin":
                    return (
                        "<h2>Subscription Required</h2>"
                        "<p>Your account does not have access to this product.</p>"
                        f"<p>Subscribed products: {', '.join(entitlements) or 'none'}</p>"
                        f"<p><a href='{sdk.auth_url}'>Manage subscriptions</a></p>"
                    ), 403

            # Store user in session (includes product_tiers for per-product tier gating)
            _tier = userinfo.get("subscription_tier") or userinfo.get("tier") or "free"
            session["kytran_user"] = {
                "sub": userinfo.get("sub"),
                "username": userinfo.get("username"),
                "email": userinfo.get("email"),
                "name": userinfo.get("name"),
                "role": userinfo.get("role"),
                "subscription_tier": _tier,
                "tier": _tier,
                "is_owner": userinfo.get("is_owner", False),
                "entitlements": entitlements,
                "product_tiers": userinfo.get("product_tiers", {}),
                "access_token": access_token,
                "refresh_token": _refresh_token_val,
                "access_token_exp": _access_token_exp,
            }

            if sdk._on_login:
                try:
                    # Pass the full userinfo from the /oauth/userinfo endpoint so
                    # callbacks receive product_tiers, language_preference, etc.
                    result = sdk._on_login(userinfo)
                    # If callback returns a Flask response (e.g. redirect for blocked users)
                    # honour it instead of continuing to next_url
                    if result is not None:
                        return result
                except Exception as e:
                    # Clear partial session so re-clicking SSO starts a fresh flow
                    session.pop("kytran_user", None)
                    return (
                        "<h2>Login Failed</h2>"
                        f"<p>An error occurred completing sign-in. Please try again.</p>"
                        f'<p><a href="/auth/kytran/login">Try again</a></p>'
                    ), 500

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
            self._refresh_if_needed()
            return f(*args, **kwargs)
        return decorated

    def get_current_user(self):
        """Return current Kytran user from session, or None."""
        return session.get("kytran_user")

    def _refresh_if_needed(self):
        """Silently refresh access token if expiring within 5 minutes."""
        auth_data = session.get("kytran_user", {})
        exp = auth_data.get("access_token_exp", 0)
        refresh_token = auth_data.get("refresh_token", "")
        if not refresh_token or not exp:
            return
        if time.time() < exp - 300:  # more than 5 minutes remaining
            return
        try:
            resp = requests.post(
                f"{self.internal_url}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.client_id,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                session["kytran_user"]["access_token"] = data["access_token"]
                session["kytran_user"]["refresh_token"] = data.get("refresh_token", refresh_token)
                session["kytran_user"]["access_token_exp"] = int(time.time()) + data.get("expires_in", 28800)
                session.modified = True
        except Exception:
            pass  # Fail silently — token expiry will handle it on the next request

    def has_entitlement(self, entitlement):
        """Check if current user has a specific product entitlement."""
        user = self.get_current_user()
        if not user:
            return False
        return entitlement in user.get("entitlements", [])
