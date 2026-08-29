"""Security regression tests for the Kytran SSO client (``kytran_auth.py``).

Covers the three P0 flaws fixed in ``fix/sso-csrf-open-redirect``:
  1. OAuth-CSRF state bypass when ``state`` is omitted (callback handler).
  2/3. Open redirect via the ``next`` parameter (already-authenticated branch
       and the callback success path).

The module under test is loaded by path from the SAME directory as this file, so
the test travels with each vendored copy of the SDK. Requires Flask + pytest;
the two already-authenticated route tests additionally use flask_login and are
skipped where it is not installed.
"""
import importlib.util
import os
from unittest import mock

import pytest
from flask import Flask

try:
    from flask_login import LoginManager, UserMixin

    _HAS_FLASK_LOGIN = True
except Exception:  # pragma: no cover - depends on the product's deps
    _HAS_FLASK_LOGIN = False

_HERE = os.path.dirname(os.path.abspath(__file__))
_AUTH_PATH = os.path.join(_HERE, "kytran_auth.py")


def _load():
    spec = importlib.util.spec_from_file_location("kytran_auth_under_test", _AUTH_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ka = _load()


def _app(with_login=False):
    app = Flask(__name__)
    app.secret_key = "test-secret-key"
    app.config.update(
        KYTRAN_CLIENT_ID="test",
        KYTRAN_CLIENT_SECRET="s3cret",
        KYTRAN_AUTH_URL="https://auth.example",
        KYTRAN_REDIRECT_URI="https://app.example/auth/kytran/callback",
    )
    if with_login and _HAS_FLASK_LOGIN:
        # Some vendored copies reference flask_login.current_user directly (with
        # and without a guard). Configure a real LoginManager + authenticated
        # user so the already-authenticated branch is taken deterministically,
        # regardless of the copy's guard style.
        lm = LoginManager()
        lm.init_app(app)

        class _TestUser(UserMixin):
            id = "1"

        @lm.user_loader
        def _loader(uid):  # pragma: no cover - trivial
            return _TestUser()

    ka.KytranAuth().init_app(app)
    return app


def _mock_oauth():
    tok = mock.Mock(status_code=200)
    tok.json.return_value = {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
    ui = mock.Mock(status_code=200)
    ui.json.return_value = {"sub": "u1", "entitlements": ["test"], "role": "admin"}
    return (
        mock.patch.object(ka.requests, "post", return_value=tok),
        mock.patch.object(ka.requests, "get", return_value=ui),
    )


# --- open-redirect guard (unit) --------------------------------------------
def test_safe_next_blocks_open_redirect():
    assert ka._safe_next("//evil.com") == "/"
    assert ka._safe_next("https://evil.com") == "/"
    assert ka._safe_next("http://evil.com") == "/"
    assert ka._safe_next("/\\evil.com") == "/"
    assert ka._safe_next(None) == "/"


def test_safe_next_allows_relative():
    assert ka._safe_next("/dashboard") == "/dashboard"
    assert ka._safe_next("/a/b?x=1&y=2") == "/a/b?x=1&y=2"


# --- CSRF state bypass (the core HIGH bug) ---------------------------------
def test_callback_missing_state_with_valid_code_is_rejected():
    """Attacker omits ``state`` but supplies a code -- must be rejected at the
    state gate (400), NOT logged in. This is the state-omission CSRF bypass."""
    app = _app()
    c = app.test_client()
    p_post, p_get = _mock_oauth()
    with p_post, p_get:
        r = c.get("/auth/kytran/callback?code=attacker_code")  # no state at all
    assert r.status_code == 400


def test_callback_forged_state_is_rejected():
    app = _app()
    c = app.test_client()
    r = c.get("/auth/kytran/callback?state=forged-not-in-store&code=abc")
    assert r.status_code == 400


# --- open redirect via next (already-authenticated branch) -----------------
@pytest.mark.skipif(not _HAS_FLASK_LOGIN, reason="flask_login not installed in this product")
def test_already_authed_login_rejects_open_redirect():
    app = _app(with_login=True)
    c = app.test_client()
    with c.session_transaction() as s:
        s["kytran_user"] = {"sub": "u1"}
        s["_user_id"] = "1"  # flask_login: authenticated
    r = c.get("/auth/kytran/login?next=//evil.com")
    assert r.status_code == 302
    assert "evil.com" not in r.headers["Location"]


@pytest.mark.skipif(not _HAS_FLASK_LOGIN, reason="flask_login not installed in this product")
def test_already_authed_login_keeps_relative_next():
    app = _app(with_login=True)
    c = app.test_client()
    with c.session_transaction() as s:
        s["kytran_user"] = {"sub": "u1"}
        s["_user_id"] = "1"
    r = c.get("/auth/kytran/login?next=/dashboard")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/dashboard")


# --- valid login still works + cannot smuggle off-site next ----------------
def test_valid_state_completes_and_redirects_relative_next():
    """A genuine login (valid state, relative next) must still succeed."""
    app = _app()
    c = app.test_client()
    state = "valid-state-token"
    ka._pending_states[state] = {"next": "/dashboard", "expires": ka.time.time() + 600, "code_verifier": "v"}
    p_post, p_get = _mock_oauth()
    with p_post, p_get:
        r = c.get(f"/auth/kytran/callback?state={state}&code=abc")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/dashboard")


def test_valid_state_cannot_smuggle_open_redirect():
    app = _app()
    c = app.test_client()
    state = "valid-state-token-2"
    ka._pending_states[state] = {"next": "//evil.com", "expires": ka.time.time() + 600, "code_verifier": "v"}
    p_post, p_get = _mock_oauth()
    with p_post, p_get:
        r = c.get(f"/auth/kytran/callback?state={state}&code=abc")
    assert r.status_code == 302
    assert "evil.com" not in r.headers["Location"]
