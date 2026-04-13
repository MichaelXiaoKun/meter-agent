"""
auth.py — Auth0 Resource Owner Password Credentials login gate for Streamlit.

Mirrors the JS get_token() flow:
  1. Check session state for a valid (non-expired) JWT.
  2. Check a local file cache (.cache.token) for a valid JWT.
  3. If neither exists, show a login form and call Auth0 /oauth/token.
  4. Cache the token and store it in session state.

Auth0 config is read from Streamlit secrets or environment variables using
the pattern: AUTH0_DOMAIN_{ENV}, AUTH0_API_AUDIENCE_{ENV}, AUTH0_CLIENT_ID_{ENV}
with AUTH0_REALM shared across environments.
Set BLUEBOT_ENV to select the environment (default: PROD).
"""

import json
import os
import time
from pathlib import Path

import httpx
import jwt
import streamlit as st

_CACHE_PATH = Path(__file__).parent / ".cache.token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _secret(key: str, default: str = "") -> str:
    """Read from env first, then Streamlit secrets (gracefully if no secrets file)."""
    val = os.environ.get(key)
    if val:
        return val
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def _auth0_config() -> dict:
    env = _secret("BLUEBOT_ENV", "PROD").upper()
    return {
        "domain":    _secret(f"AUTH0_DOMAIN_{env}"),
        "audience":  _secret(f"AUTH0_API_AUDIENCE_{env}"),
        "client_id": _secret(f"AUTH0_CLIENT_ID_{env}"),
        "realm":     _secret("AUTH0_REALM"),
    }


def _token_valid(token: str) -> bool:
    """Return True if the JWT is present and not expired."""
    if not token:
        return False
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get("exp", 0) > time.time()
    except Exception:
        return False


def _load_cached_token() -> tuple[str, str] | tuple[None, None]:
    """Return (username, token) from the file cache, or (None, None)."""
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        token = data.get("token", "")
        if _token_valid(token):
            return data.get("username", ""), token
    except Exception:
        pass
    return None, None


def _save_cached_token(username: str, token: str) -> None:
    try:
        _CACHE_PATH.write_text(
            json.dumps({"username": username, "token": token}),
            encoding="utf-8",
        )
    except Exception:
        pass


def _clear_cached_token() -> None:
    try:
        _CACHE_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _authenticate(cfg: dict, username: str, password: str) -> tuple[str | None, str | None]:
    """Call Auth0 ROPC endpoint. Returns (access_token, None) or (None, error_msg)."""
    try:
        resp = httpx.post(
            f"{cfg['domain']}/oauth/token",
            json={
                "client_id":  cfg["client_id"],
                "grant_type": "http://auth0.com/oauth/grant-type/password-realm",
                "username":   username,
                "password":   password,
                "audience":   cfg["audience"],
                "realm":      cfg["realm"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["access_token"], None
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("error_description", str(e))
        except Exception:
            msg = str(e)
        return None, msg
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def login_gate() -> str:
    """
    Enforce authentication before the main app renders.

    Returns the bearer token if already authenticated.
    Renders the login form and calls st.stop() if not — so callers can
    simply do:  token = auth.login_gate()
    """
    # 1. Valid token already in session state
    token = st.session_state.get("auth_token", "")
    if _token_valid(token):
        return token

    # 2. Valid token in file cache
    username, cached_token = _load_cached_token()
    if cached_token:
        st.session_state.auth_token  = cached_token
        st.session_state.auth_user   = username
        return cached_token

    # 3. Show login form — does not return; calls st.stop() after rendering
    _render_login_form()
    st.stop()


def logout() -> None:
    """Clear the in-session and cached token, then rerun to show the login page."""
    st.session_state.pop("auth_token", None)
    st.session_state.pop("auth_user",  None)
    _clear_cached_token()
    st.rerun()


# ---------------------------------------------------------------------------
# Login form UI
# ---------------------------------------------------------------------------

def _render_login_form() -> None:
    st.markdown(
        """
        <style>
        /* Hide the default Streamlit header/footer on the login page */
        header[data-testid="stHeader"] { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Centre a card
    _, col, _ = st.columns([1, 1.4, 1])
    with col:
        st.markdown(
            "<h2 style='text-align:center; margin-bottom:0.25rem;'>💧 bluebot Assistant</h2>"
            "<p style='text-align:center; color:grey; margin-bottom:2rem;'>Sign in to continue</p>",
            unsafe_allow_html=True,
        )

        with st.form("login_form", border=True):
            username  = st.text_input("Username", placeholder="you@example.com")
            password  = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", use_container_width=True)

        if submitted:
            if not username or not password:
                st.error("Please enter both username and password.")
                return

            cfg = _auth0_config()
            if not cfg["domain"] or not cfg["client_id"]:
                st.error(
                    "Auth0 is not configured. Set AUTH0_DOMAIN_{ENV}, "
                    "AUTH0_CLIENT_ID_{ENV}, AUTH0_API_AUDIENCE_{ENV}, "
                    "and AUTH0_REALM in your Streamlit secrets."
                )
                return

            with st.spinner("Signing in…"):
                token, error = _authenticate(cfg, username, password)

            if token:
                st.session_state.auth_token = token
                st.session_state.auth_user  = username
                _save_cached_token(username, token)
                st.rerun()
            else:
                st.error(f"Login failed: {error}")
