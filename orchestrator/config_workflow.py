"""Lightweight confirmation guard for meter configuration writes.

Write tools touch real devices. The orchestrator can propose those writes, but
the UI must confirm the exact pending action before execution.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

DEFAULT_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class PendingConfigAction:
    action_id: str
    conversation_id: str
    user_scope: str
    tool_name: str
    inputs: dict[str, Any]
    canonical_inputs: str
    created_at: float
    expires_at: float
    current_values: dict[str, Any] | None = None

    def as_workflow(self, *, status: str = "pending_confirmation") -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "status": status,
            "tool": self.tool_name,
            "serial_number": str(self.inputs.get("serial_number") or ""),
            "proposed_values": deepcopy(self.inputs),
            "current_values": deepcopy(self.current_values) if self.current_values else None,
            "created_at": int(self.created_at),
            "expires_at": int(self.expires_at),
            "expires_in_seconds": max(0, int(self.expires_at - time.time())),
        }


_lock = threading.Lock()
_pending: dict[tuple[str, str, str], PendingConfigAction] = {}


def user_scope_from_token(token: str | None) -> str:
    raw = (token or "").strip()
    if not raw:
        return "anonymous"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def canonical_inputs(inputs: dict[str, Any]) -> str:
    """Stable exact-match string for a proposed write payload."""
    return json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=str)


def _key(conversation_id: str, user_scope: str, action_id: str) -> tuple[str, str, str]:
    return (conversation_id or "default", user_scope or "anonymous", action_id)


def _gc(now: float | None = None) -> None:
    ts = time.time() if now is None else now
    expired = [k for k, v in _pending.items() if v.expires_at <= ts]
    for k in expired:
        _pending.pop(k, None)


def create_pending_action(
    *,
    conversation_id: str,
    user_scope: str,
    tool_name: str,
    inputs: dict[str, Any],
    current_values: dict[str, Any] | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> PendingConfigAction:
    now = time.time()
    action = PendingConfigAction(
        action_id=uuid.uuid4().hex[:12],
        conversation_id=conversation_id or "default",
        user_scope=user_scope or "anonymous",
        tool_name=tool_name,
        inputs=deepcopy(inputs),
        canonical_inputs=canonical_inputs(inputs),
        current_values=deepcopy(current_values) if current_values else None,
        created_at=now,
        expires_at=now + max(1, int(ttl_seconds)),
    )
    with _lock:
        _gc(now)
        _pending[_key(action.conversation_id, action.user_scope, action.action_id)] = action
    return action


def get_pending_action(
    conversation_id: str,
    user_scope: str,
    action_id: str,
) -> PendingConfigAction | None:
    with _lock:
        _gc()
        return _pending.get(_key(conversation_id, user_scope, action_id))


def consume_pending_action(
    conversation_id: str,
    user_scope: str,
    action_id: str,
) -> PendingConfigAction | None:
    with _lock:
        _gc()
        return _pending.pop(_key(conversation_id, user_scope, action_id), None)


def validate_pending_action(
    action: PendingConfigAction,
    *,
    tool_name: str,
    inputs: dict[str, Any],
) -> tuple[bool, str | None]:
    if action.expires_at <= time.time():
        return False, "This configuration confirmation expired. Please review the change again."
    if action.tool_name != tool_name:
        return False, "The confirmed action does not match this configuration tool."
    if action.canonical_inputs != canonical_inputs(inputs):
        return False, "The confirmed action values changed. Please review the new values before applying them."
    return True, None


def clear_pending_actions_for_tests() -> None:
    with _lock:
        _pending.clear()
