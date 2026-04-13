"""
auth.py — Auth0 Resource Owner Password Credentials login gate for Streamlit.

Token persistence strategy:
  1. Check st.session_state for a valid (non-expired) JWT  →  fastest path.
  2. Check the browser cookie "bluebot_token"              →  survives page refresh.
  3. Show the login form → call Auth0 /oauth/token → store in both.

Each browser has its own isolated cookie store, so different browsers /
users go through their own independent login flow and stay logged in until
their JWT actually expires.

Auth0 config is read from Streamlit secrets or environment variables using
the pattern: AUTH0_DOMAIN_{ENV}, AUTH0_API_AUDIENCE_{ENV}, AUTH0_CLIENT_ID_{ENV}
with AUTH0_REALM shared across environments.
Set BLUEBOT_ENV to select the environment (default: PROD).
"""

import os
import time

import httpx
import jwt
import streamlit as st

_COOKIE_TOKEN = "bluebot_token"
_COOKIE_USER  = "bluebot_user"


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


def _token_max_age(token: str) -> int:
    """Return seconds until the JWT expires (minimum 0)."""
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        return max(0, int(payload.get("exp", 0) - time.time()))
    except Exception:
        return 0


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

def login_gate(cookies=None) -> str:
    """
    Enforce authentication before the main app renders.

    Pass the CookieController instance so the token can be read from / written
    to the browser cookie (survives page refreshes, isolated per browser).

    Returns the bearer token if already authenticated.
    Renders the login form and calls st.stop() if not.
    """
    # 1. Fast path — valid token already in session state
    token = st.session_state.get("auth_token", "")
    if _token_valid(token):
        return token

    # 2. Restore from browser cookie (survives hard refresh)
    if cookies is not None:
        try:
            cookie_token = cookies.get(_COOKIE_TOKEN) or ""
            cookie_user  = cookies.get(_COOKIE_USER)  or ""
            if _token_valid(cookie_token):
                st.session_state.auth_token = cookie_token
                st.session_state.auth_user  = cookie_user
                return cookie_token
        except Exception:
            pass  # cookies not yet available on first render cycle

    # 3. Show login form — does not return
    _render_login_form(cookies)
    st.stop()


def logout(cookies=None) -> None:
    """Clear the in-session token and browser cookie, then rerun to show the login page."""
    st.session_state.pop("auth_token", None)
    st.session_state.pop("auth_user",  None)
    if cookies is not None:
        try:
            cookies.remove(_COOKIE_TOKEN)
            cookies.remove(_COOKIE_USER)
        except Exception:
            pass
    st.rerun()


# ---------------------------------------------------------------------------
# Login form UI
# ---------------------------------------------------------------------------

def _render_login_form(cookies=None) -> None:
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
                # Persist to browser cookie so page refreshes don't log out.
                # Cookie expires when the JWT itself expires.
                if cookies is not None:
                    try:
                        max_age = _token_max_age(token)
                        cookies.set(_COOKIE_TOKEN, token,   max_age=max_age)
                        cookies.set(_COOKIE_USER,  username, max_age=max_age)
                    except Exception:
                        pass  # cookie write failure is non-fatal
                st.rerun()
            else:
                st.error(f"Login failed: {error}")
