"""Auth routes."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from .. import app as app_runtime

router = APIRouter(tags=["auth"])


@router.post("/api/auth/login")
async def login(body: app_runtime.LoginRequest):
    """Proxy Auth0 ROPC login — keeps client_id/audience server-side."""
    cfg = app_runtime._auth0_config()
    env = app_runtime._env("BLUEBOT_ENV", "PROD").upper()
    if not cfg["domain"] or not cfg["client_id"] or not cfg["audience"]:
        missing = []
        if not cfg["domain"]:
            missing.append(f"AUTH0_DOMAIN_{env}")
        if not cfg["client_id"]:
            missing.append(f"AUTH0_CLIENT_ID_{env}")
        if not cfg["audience"]:
            missing.append(f"AUTH0_API_AUDIENCE_{env}")
        raise HTTPException(
            500,
            "Auth0 is not configured on the server. Set these in your host environment "
            f"(e.g. Railway Variables): {', '.join(missing)}. "
            f"BLUEBOT_ENV is {env!r} — variable names must use that suffix.",
        )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{cfg['domain']}/oauth/token",
                json={
                    "client_id":  cfg["client_id"],
                    "grant_type": "http://auth0.com/oauth/grant-type/password-realm",
                    "username":   body.username,
                    "password":   body.password,
                    "audience":   cfg["audience"],
                    "realm":      cfg["realm"],
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "access_token": data["access_token"],
                "user": body.username,
            }
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("error_description", str(e))
        except Exception:
            msg = str(e)
        raise HTTPException(401, msg)
    except Exception as e:
        raise HTTPException(502, str(e))


@router.post("/api/auth/forgot-password")
async def forgot_password(body: app_runtime.ForgotPasswordRequest):
    """Proxy Auth0 ``/dbconnections/change_password`` — same flow as bluebot-saas ``changePassword``."""
    cfg = app_runtime._auth0_config()
    env = app_runtime._env("BLUEBOT_ENV", "PROD").upper()
    if not cfg["domain"] or not cfg["client_id"]:
        missing = []
        if not cfg["domain"]:
            missing.append(f"AUTH0_DOMAIN_{env}")
        if not cfg["client_id"]:
            missing.append(f"AUTH0_CLIENT_ID_{env}")
        raise HTTPException(
            500,
            "Auth0 is not configured on the server. Set these in your host environment "
            f"(e.g. Railway Variables): {', '.join(missing)}. "
            f"BLUEBOT_ENV is {env!r} — variable names must use that suffix.",
        )

    email = body.email.strip()
    if not email:
        raise HTTPException(400, "Email is required")

    try:
        base = (cfg["domain"] or "").rstrip("/")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base}/dbconnections/change_password",
                json={
                    "client_id": cfg["client_id"],
                    "email": email,
                    "connection": cfg["realm"],
                },
                timeout=15,
            )
            if resp.is_error:
                err_msg = "Password reset could not be started"
                try:
                    j = resp.json()
                    d = j.get("description") or j.get("error_description") or j.get("message")
                    if isinstance(d, str) and d:
                        err_msg = d
                    elif j.get("error") and isinstance(j.get("error"), str):
                        err_msg = j["error"]
                except Exception:
                    err_msg = resp.text or err_msg
                code = 400 if resp.status_code < 500 else 502
                raise HTTPException(code, err_msg)
        return {"ok": True}
    except HTTPException:
        raise
    except httpx.RequestError as e:
        # DNS / TLS / connection to Auth0 — clearer than a raw httpx string.
        err_s = str(e)
        if any(
            part in err_s
            for part in (
                "Name or service not known",
                "getaddrinfo",
                "nodename nor servname",
                "Could not connect",
                "ConnectError",
                "Connection refused",
            )
        ):
            raise HTTPException(
                502,
                f"Could not reach Auth0 at {base!r}. Check AUTH0_DOMAIN_{env} (correct URL) "
                f"and network/DNS from the host that runs the orchestrator.",
            ) from e
        raise HTTPException(502, err_s) from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e
