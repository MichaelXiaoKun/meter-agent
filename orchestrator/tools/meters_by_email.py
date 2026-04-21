"""
meters_by_email.py — Orchestrator tool for listing every meter attached to a
bluebot user account, keyed by the account's email address.

Under the hood this chains **three** management-API calls so the caller only
ever needs to supply an email:

    1. GET /management/v1/accounts?email=<email>
          → pick the first account's ``id`` (accountId)
    2. GET /management/v1/account/{accountId}/organizations
          → pick the entry where ``role == "owner"`` and read its
            ``organizationId`` (the account's owner organization)
    3. GET /management/v1/device?organizationId=<organizationId>
          → list the owner-organization's meters

Each stage has its own error taxonomy (see ``error_stage`` / ``error_code``
on the return value) so the orchestrator can tell the user *where* the
lookup failed without exposing HTTP internals.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from tools.meter_profile import classify_network_type

_DEFAULT_MANAGEMENT_BASE = "https://prod.bluebot.com"
_ADMIN_HEADERS = {"x-admin-query": "true"}

_DEFAULT_RESULT_CAP = 100
_MAX_RESULT_CAP = 500


TOOL_DEFINITION = {
    "name": "list_meters_for_account",
    "description": (
        "List every meter attached to a bluebot user account, keyed by the "
        "account's email address. The user must supply the email verbatim "
        "(e.g. 'what meters does alice@acme.com have?'). Returns a compact "
        "per-meter summary (serialNumber, label, model, network_type, "
        "commissioned/installed flags, device timezone). For details on a "
        "single meter, follow up with get_meter_profile or check_meter_status "
        "using the desired serialNumber."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "email": {
                "type": "string",
                "description": (
                    "Email address of the bluebot user account to look up "
                    "(passed verbatim from the user's message)."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Optional maximum number of meter rows to return "
                    f"(default {_DEFAULT_RESULT_CAP}, hard max {_MAX_RESULT_CAP}). "
                    "If more meters exist, the result is truncated and "
                    "``truncated`` is set to true."
                ),
                "minimum": 1,
                "maximum": _MAX_RESULT_CAP,
            },
        },
        "required": ["email"],
    },
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _management_base_url() -> str:
    return os.environ.get("BLUEBOT_MANAGEMENT_BASE", _DEFAULT_MANAGEMENT_BASE).rstrip("/")


def _headers(token: str) -> Dict[str, str]:
    return {**_ADMIN_HEADERS, "Authorization": f"Bearer {token}"}


def _fail(
    email: str,
    *,
    stage: str | None,
    code: str,
    message: str,
    account_id: str | None = None,
    owner_organization_id: str | None = None,
) -> Dict[str, Any]:
    return {
        "success": False,
        "email": email,
        "error_stage": stage,
        "error_code": code,
        "error": message,
        "account_id": account_id,
        "owner_organization_id": owner_organization_id,
        "total_count": 0,
        "returned_count": 0,
        "truncated": False,
        "meters": [],
        "notice": None,
    }


def _http_error_message(
    stage_label: str,
    exc: httpx.HTTPStatusError,
    *,
    email: str,
) -> Tuple[str, str]:
    """Return ``(error_code, human_message)`` for an HTTPStatusError.

    ``stage_label`` is the user-facing noun for this step (e.g. "account
    lookup", "organization lookup", "meter lookup") — it's inlined into the
    returned message so error strings stay grep-friendly.
    """
    code = exc.response.status_code
    body = (exc.response.text or "")[:300].strip()

    if code == 401:
        return (
            "http_401",
            (
                f"bluebot {stage_label} failed: authorization was rejected (HTTP 401). "
                "Your session may have expired — please sign in again."
            ),
        )
    if code == 403:
        return (
            "http_403",
            (
                f"bluebot {stage_label} failed: this session is not allowed to read "
                f"these records (HTTP 403)."
            ),
        )
    if code == 404:
        return (
            "http_404",
            f"No bluebot record found for {email!r} during {stage_label} (HTTP 404).",
        )
    return (
        "http_error",
        f"bluebot {stage_label} failed (HTTP {code}): {body or '(empty body)'}",
    )


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------

def _lookup_account_id(
    client: httpx.Client, email: str, token: str
) -> Tuple[str | None, Dict[str, Any] | None]:
    """Stage 1: email → accountId. Returns (accountId, failure_dict)."""
    url = f"{_management_base_url()}/management/v1/accounts"
    try:
        resp = client.get(url, headers=_headers(token), params={"email": email})
        resp.raise_for_status()
        payload: Any = resp.json()
    except httpx.HTTPStatusError as exc:
        code, msg = _http_error_message("account lookup", exc, email=email)
        return None, _fail(email, stage="account_lookup", code=code, message=msg)
    except (httpx.HTTPError, ValueError) as exc:
        return None, _fail(
            email,
            stage="account_lookup",
            code="network_error",
            message=f"bluebot account lookup failed: {type(exc).__name__}: {exc}",
        )

    rows: List[Dict[str, Any]] = [
        r for r in (payload if isinstance(payload, list) else []) if isinstance(r, dict)
    ]
    if not rows:
        return None, _fail(
            email,
            stage="account_lookup",
            code="not_found",
            message=f"No bluebot account found for {email!r}.",
        )

    account_id = rows[0].get("id")
    if not account_id:
        return None, _fail(
            email,
            stage="account_lookup",
            code="not_found",
            message=f"No usable account id on the bluebot record for {email!r}.",
        )
    return str(account_id), None


def _lookup_owner_organization(
    client: httpx.Client, account_id: str, token: str, email: str
) -> Tuple[str | None, Dict[str, Any] | None]:
    """Stage 2: accountId → owner organizationId. Returns (orgId, failure_dict).

    The user noted that a bluebot account is expected to have at most one
    owner organization, so we take the first ``role == "owner"`` row if
    several ever appear.
    """
    url = f"{_management_base_url()}/management/v1/account/{account_id}/organizations"
    try:
        resp = client.get(url, headers=_headers(token))
        resp.raise_for_status()
        payload: Any = resp.json()
    except httpx.HTTPStatusError as exc:
        code, msg = _http_error_message("organization lookup", exc, email=email)
        return None, _fail(
            email, stage="organization_lookup", code=code, message=msg, account_id=account_id
        )
    except (httpx.HTTPError, ValueError) as exc:
        return None, _fail(
            email,
            stage="organization_lookup",
            code="network_error",
            message=(
                f"bluebot organization lookup failed: {type(exc).__name__}: {exc}"
            ),
            account_id=account_id,
        )

    rows: List[Dict[str, Any]] = [
        r for r in (payload if isinstance(payload, list) else []) if isinstance(r, dict)
    ]
    owner = next(
        (r for r in rows if str(r.get("role") or "").lower() == "owner"),
        None,
    )
    if owner is None:
        return None, _fail(
            email,
            stage="organization_lookup",
            code="no_owner_role",
            message=(
                f"Found the bluebot account for {email!r} but no organization on it "
                "has an owner role — so there are no meters this account owns."
            ),
            account_id=account_id,
        )
    org_id = owner.get("organizationId")
    if not org_id:
        return None, _fail(
            email,
            stage="organization_lookup",
            code="no_owner_role",
            message=(
                f"Found an owner-role entry on {email!r}'s bluebot account but it is "
                "missing an organization id."
            ),
            account_id=account_id,
        )
    return str(org_id), None


def _pick_meter_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Compact per-device subset — all rows come from the same owner org so we
    skip the per-row organization echo to keep results small."""
    keys = [
        "serialNumber",
        "label",
        "model",
        "category",
        "deviceType",
        "networkUniqueIdentifier",
        "commissioned",
        "installed",
        "installedOn",
        "active",
        "deviceTimeZone",
    ]
    out: Dict[str, Any] = {k: row.get(k) for k in keys if k in row}
    classification = classify_network_type(
        row.get("serialNumber") or "",
        row.get("networkUniqueIdentifier"),
    )
    out["network_type"] = classification["network_type"]
    return out


def _lookup_owner_meters(
    client: httpx.Client,
    organization_id: str,
    token: str,
    email: str,
    account_id: str,
) -> Tuple[List[Dict[str, Any]] | None, Dict[str, Any] | None]:
    """Stage 3: organizationId → devices. Returns (rows, failure_dict)."""
    url = f"{_management_base_url()}/management/v1/device"
    try:
        resp = client.get(
            url, headers=_headers(token), params={"organizationId": organization_id}
        )
        resp.raise_for_status()
        payload: Any = resp.json()
    except httpx.HTTPStatusError as exc:
        code, msg = _http_error_message("meter lookup", exc, email=email)
        return None, _fail(
            email,
            stage="device_lookup",
            code=code,
            message=msg,
            account_id=account_id,
            owner_organization_id=organization_id,
        )
    except (httpx.HTTPError, ValueError) as exc:
        return None, _fail(
            email,
            stage="device_lookup",
            code="network_error",
            message=f"bluebot meter lookup failed: {type(exc).__name__}: {exc}",
            account_id=account_id,
            owner_organization_id=organization_id,
        )

    rows: List[Dict[str, Any]] = [
        r for r in (payload if isinstance(payload, list) else []) if isinstance(r, dict)
    ]
    return rows, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def list_meters_for_account(
    email: str,
    token: str,
    *,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Resolve *email* → owning account → owner organization → meter list.

    Returns a dict with a stable shape for both success and failure so the
    orchestrator never has to deal with raised exceptions:

        {
            "success":                bool,
            "email":                  str,
            "error_stage":            "account_lookup" | "organization_lookup" |
                                      "device_lookup" | None,
            "error_code":             str | None,  # see module docstring table
            "error":                  str | None,  # human-readable, can be relayed verbatim
            "account_id":             str | None,
            "owner_organization_id":  str | None,
            "total_count":            int,
            "returned_count":         int,
            "truncated":              bool,
            "meters":                 list[dict],
            "notice":                 str | None,  # e.g. "No meters found…"
        }
    """
    cleaned_email = (email or "").strip()
    if not cleaned_email:
        return _fail(
            cleaned_email,
            stage=None,
            code="missing_email",
            message="Email is required — ask the user to provide it.",
        )
    if not token:
        return _fail(
            cleaned_email,
            stage=None,
            code="no_token",
            message="Bearer token required for the bluebot management API.",
        )

    cap = _DEFAULT_RESULT_CAP if limit is None else max(1, min(int(limit), _MAX_RESULT_CAP))

    with httpx.Client(timeout=15) as client:
        account_id, failure = _lookup_account_id(client, cleaned_email, token)
        if failure is not None:
            return failure
        assert account_id is not None

        organization_id, failure = _lookup_owner_organization(
            client, account_id, token, cleaned_email
        )
        if failure is not None:
            return failure
        assert organization_id is not None

        rows, failure = _lookup_owner_meters(
            client, organization_id, token, cleaned_email, account_id
        )
        if failure is not None:
            return failure

    assert rows is not None
    total = len(rows)
    if total == 0:
        return {
            "success": True,
            "email": cleaned_email,
            "error_stage": None,
            "error_code": None,
            "error": None,
            "account_id": account_id,
            "owner_organization_id": organization_id,
            "total_count": 0,
            "returned_count": 0,
            "truncated": False,
            "meters": [],
            "notice": (
                f"No meters found on {cleaned_email}'s bluebot account."
            ),
        }

    selected = rows[:cap]
    meters = [_pick_meter_fields(r) for r in selected]
    return {
        "success": True,
        "email": cleaned_email,
        "error_stage": None,
        "error_code": None,
        "error": None,
        "account_id": account_id,
        "owner_organization_id": organization_id,
        "total_count": total,
        "returned_count": len(meters),
        "truncated": total > cap,
        "meters": meters,
        "notice": None,
    }
