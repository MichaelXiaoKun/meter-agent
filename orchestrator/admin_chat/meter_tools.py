"""Tool registry for the meter orchestrator agent."""

from __future__ import annotations

from processors.time_range import TOOL_DEFINITION as _TIME_RANGE_DEF, resolve_time_range
from tools.meter_status import TOOL_DEFINITION as _METER_STATUS_DEF, check_meter_status
from tools.meter_profile import TOOL_DEFINITION as _METER_PROFILE_DEF, get_meter_profile
from tools.meters_by_email import (
    TOOL_DEFINITION as _METERS_BY_EMAIL_DEF,
    list_meters_for_account,
)
from tools.meter_compare import TOOL_DEFINITION as _METER_COMPARE_DEF, compare_meters
from tools.fleet_health import (
    TOOL_DEFINITION as _FLEET_HEALTH_DEF,
    rank_fleet_by_health,
)
from tools.fleet_triage import (
    TOOL_DEFINITION as _FLEET_TRIAGE_DEF,
    triage_fleet_for_account,
)
from tools.period_compare import (
    TOOL_DEFINITION as _PERIOD_COMPARE_DEF,
    compare_periods,
)
from tools.flow_analysis import TOOL_DEFINITION as _FLOW_ANALYSIS_DEF, analyze_flow_data
from tools.pipe_configuration import (
    TOOL_DEFINITION as _PIPE_CONFIGURATION_DEF,
    configure_meter_pipe,
)
from tools.set_transducer_angle import (
    TOOL_DEFINITION as _SET_TRANSDUCER_ANGLE_DEF,
    set_transducer_angle_only,
)
from tools.sweep_transducer_angles import (
    TOOL_DEFINITION as _SWEEP_TRANSDUCER_ANGLES_DEF,
    sweep_transducer_angles,
)
from tools.set_zero_point import (
    TOOL_DEFINITION as _SET_ZERO_POINT_DEF,
    set_zero_point,
)
from tools.batch_flow_analysis import (
    TOOL_DEFINITION as _BATCH_FLOW_ANALYSIS_DEF,
    batch_analyze_flow,
)
from tools.tickets import (
    CREATE_TICKET_TOOL_DEFINITION as _CREATE_TICKET_DEF,
    LIST_TICKETS_TOOL_DEFINITION as _LIST_TICKETS_DEF,
    UPDATE_TICKET_TOOL_DEFINITION as _UPDATE_TICKET_DEF,
    create_ticket,
    list_tickets,
    update_ticket,
)

from shared.tool_registry import Tool, ToolRegistry

METER_REGISTRY = ToolRegistry()


def _register_meter_tool(
    definition: dict,
    handler,
    *,
    context_params=None,
    is_write: bool = False,
    is_serial_only: bool = False,
    is_dedupable_read: bool = False,
    is_heartbeat_progress: bool = False,
) -> None:
    """Helper to register a meter tool with metadata."""
    if context_params is None:
        context_params = frozenset()
    METER_REGISTRY.register(Tool(
        definition=definition,
        handler=handler,
        context_params=frozenset(context_params),
        is_write=is_write,
        is_serial_only=is_serial_only,
        is_dedupable_read=is_dedupable_read,
        is_heartbeat_progress=is_heartbeat_progress,
    ))


# ---- Read-only tools ----

# resolve_time_range: takes client_timezone (→ user_timezone) + anthropic_api_key
_register_meter_tool(
    _TIME_RANGE_DEF,
    lambda description, *, client_timezone=None, anthropic_api_key=None: resolve_time_range(
        description,
        user_timezone=client_timezone,
        anthropic_api_key=anthropic_api_key,
    ),
    context_params=["client_timezone", "anthropic_api_key"],
    is_dedupable_read=True,
)

# check_meter_status: takes token + anthropic_api_key
_register_meter_tool(
    _METER_STATUS_DEF,
    lambda serial_number, *, token, anthropic_api_key=None: check_meter_status(
        serial_number,
        token,
        anthropic_api_key=anthropic_api_key,
    ),
    context_params=["token", "anthropic_api_key"],
    is_dedupable_read=True,
)

# get_meter_profile: takes token
_register_meter_tool(
    _METER_PROFILE_DEF,
    lambda serial_number, *, token: get_meter_profile(serial_number, token),
    context_params=["token"],
    is_dedupable_read=True,
)

# list_meters_for_account: takes token
_register_meter_tool(
    _METERS_BY_EMAIL_DEF,
    lambda email, limit=None, *, token: list_meters_for_account(
        email,
        token,
        limit=limit,
    ),
    context_params=["token"],
    is_dedupable_read=True,
)

# compare_meters: takes token + anthropic_api_key
_register_meter_tool(
    _METER_COMPARE_DEF,
    lambda serial_numbers, *, token, anthropic_api_key=None: compare_meters(
        serial_numbers,
        token,
        anthropic_api_key=anthropic_api_key,
    ),
    context_params=["token", "anthropic_api_key"],
    is_dedupable_read=True,
)

# rank_fleet_by_health: takes token + anthropic_api_key, emits progress
_register_meter_tool(
    _FLEET_HEALTH_DEF,
    lambda serial_numbers, *, token, anthropic_api_key=None: rank_fleet_by_health(
        serial_numbers,
        token,
        anthropic_api_key=anthropic_api_key,
    ),
    context_params=["token", "anthropic_api_key"],
    is_dedupable_read=True,
    is_heartbeat_progress=True,
)

# triage_fleet_for_account: takes token + anthropic_api_key, emits progress
_register_meter_tool(
    _FLEET_TRIAGE_DEF,
    lambda email, *, token, anthropic_api_key=None: triage_fleet_for_account(
        email,
        token,
        anthropic_api_key=anthropic_api_key,
    ),
    context_params=["token", "anthropic_api_key"],
    is_dedupable_read=True,
    is_heartbeat_progress=True,
)

# compare_periods: takes token + client_timezone (→ display_timezone) + anthropic_api_key
_register_meter_tool(
    _PERIOD_COMPARE_DEF,
    lambda serial_number, period_a, period_b, network_type=None, meter_timezone=None, *, token, client_timezone=None, anthropic_api_key=None: compare_periods(
        serial_number,
        period_a,
        period_b,
        token,
        display_timezone=client_timezone,
        anthropic_api_key=anthropic_api_key,
        network_type=network_type,
        meter_timezone=meter_timezone,
    ),
    context_params=["token", "client_timezone", "anthropic_api_key"],
    is_dedupable_read=True,
)

# analyze_flow_data: takes token + client_timezone (→ display_timezone) + anthropic_api_key, serial-only
_register_meter_tool(
    _FLOW_ANALYSIS_DEF,
    lambda serial_number, start, end, network_type=None, meter_timezone=None, analysis_mode=None, baseline_window=None, filters=None, event_predicates=None, *, token, client_timezone=None, anthropic_api_key=None: analyze_flow_data(
        serial_number,
        start,
        end,
        token,
        display_timezone=client_timezone,
        anthropic_api_key=anthropic_api_key,
        network_type=network_type,
        meter_timezone=meter_timezone,
        analysis_mode=analysis_mode,
        baseline_window=baseline_window,
        filters=filters,
        event_predicates=event_predicates,
    ),
    context_params=["token", "client_timezone", "anthropic_api_key"],
    is_dedupable_read=True,
    is_serial_only=True,
)

# batch_analyze_flow: takes token + client_timezone (→ display_timezone) + anthropic_api_key
_register_meter_tool(
    _BATCH_FLOW_ANALYSIS_DEF,
    lambda serial_numbers, start, end, network_type=None, *, token, client_timezone=None, anthropic_api_key=None: batch_analyze_flow(
        serial_numbers,
        start,
        end,
        token,
        display_timezone=client_timezone,
        anthropic_api_key=anthropic_api_key,
        network_type=network_type,
    ),
    context_params=["token", "client_timezone", "anthropic_api_key"],
)

# ---- Write tools (mutations, serial-only) ----

# configure_meter_pipe: takes token + anthropic_api_key, write, serial-only
_register_meter_tool(
    _PIPE_CONFIGURATION_DEF,
    lambda serial_number, action, value=None, *, token, anthropic_api_key=None: configure_meter_pipe(
        serial_number,
        action,
        value=value,
        token=token,
        anthropic_api_key=anthropic_api_key,
    ),
    context_params=["token", "anthropic_api_key"],
    is_write=True,
    is_serial_only=True,
)

# set_transducer_angle_only: takes token + anthropic_api_key, write, serial-only
_register_meter_tool(
    _SET_TRANSDUCER_ANGLE_DEF,
    lambda serial_number, angle_degrees, *, token, anthropic_api_key=None: set_transducer_angle_only(
        serial_number,
        angle_degrees,
        token,
        anthropic_api_key=anthropic_api_key,
    ),
    context_params=["token", "anthropic_api_key"],
    is_write=True,
    is_serial_only=True,
)

# sweep_transducer_angles: takes token + anthropic_api_key, write, serial-only
# This tool injects functions to call other tools; those are stable intra-package deps
_register_meter_tool(
    _SWEEP_TRANSDUCER_ANGLES_DEF,
    lambda serial_number, min_angle=None, max_angle=None, step=None, *, token, anthropic_api_key=None: sweep_transducer_angles(
        serial_number,
        min_angle=min_angle,
        max_angle=max_angle,
        step=step,
        token=token,
        anthropic_api_key=anthropic_api_key,
        profile_lookup=get_meter_profile,
        set_angle_func=set_transducer_angle_only,
        check_status_func=check_meter_status,
    ),
    context_params=["token", "anthropic_api_key"],
    is_write=True,
    is_serial_only=True,
)

# set_zero_point: takes token + anthropic_api_key, write, serial-only
_register_meter_tool(
    _SET_ZERO_POINT_DEF,
    lambda serial_number, action=None, mqtt_payload=None, *, token, anthropic_api_key=None: set_zero_point(
        serial_number,
        token,
        action=action,
        mqtt_payload=mqtt_payload,
        anthropic_api_key=anthropic_api_key,
    ),
    context_params=["token", "anthropic_api_key"],
    is_write=True,
    is_serial_only=True,
)

# ---- Ticket tools (serial-only, take conversation_id from context) ----

def _list_tickets_handler(
    conversation_id=None,
    serial_number=None,
    status=None,
    all_conversations=False,
) -> dict:
    """list_tickets wrapper that optionally filters by multiple fields."""
    from shared.observability import current_turn_id
    return list_tickets(
        conversation_id=conversation_id,
        serial_number=serial_number,
        status=status,
        all_conversations=bool(all_conversations),
    )


def _create_ticket_handler(
    title,
    success_criteria=None,
    description="",
    serial_number=None,
    priority="normal",
    owner_type=None,
    owner_id=None,
    agent_checkable=False,
    due_at=None,
    metadata=None,
    evidence=None,
    *,
    conversation_id,
) -> dict:
    """create_ticket wrapper that accepts all LLM input fields + conversation_id context."""
    from shared.observability import current_turn_id
    return create_ticket(
        conversation_id=conversation_id,
        title=title,
        success_criteria=success_criteria,
        description=description or "",
        serial_number=serial_number,
        priority=priority or "normal",
        owner_type=owner_type,
        owner_id=owner_id,
        agent_checkable=bool(agent_checkable),
        due_at=due_at,
        metadata=metadata,
        evidence=evidence,
        turn_id=current_turn_id(),
    )


def _update_ticket_handler(
    ticket_id,
    title=None,
    description=None,
    success_criteria=None,
    status=None,
    priority=None,
    owner_type=None,
    owner_id=None,
    due_at=None,
    serial_number=None,
    metadata=None,
    note="",
    evidence=None,
    *,
    conversation_id,
) -> dict:
    """update_ticket wrapper that accepts all LLM input fields + conversation_id context."""
    from shared.observability import current_turn_id
    return update_ticket(
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        title=title,
        description=description,
        success_criteria=success_criteria,
        status=status,
        priority=priority,
        owner_type=owner_type,
        owner_id=owner_id,
        due_at=due_at,
        serial_number=serial_number,
        metadata=metadata,
        note=note or "",
        evidence=evidence,
        turn_id=current_turn_id(),
    )


# list_tickets: takes conversation_id, serial-only
_register_meter_tool(
    _LIST_TICKETS_DEF,
    _list_tickets_handler,
    context_params=["conversation_id"],
    is_serial_only=True,
)

# create_ticket: takes conversation_id and mutates the local workflow store.
# It is serial-only for ordering, but it is not a device/configuration write and
# should not trigger the meter configuration confirmation gate.
_register_meter_tool(
    _CREATE_TICKET_DEF,
    _create_ticket_handler,
    context_params=["conversation_id"],
    is_serial_only=True,
)

# update_ticket: takes conversation_id and mutates the local workflow store.
# It is serial-only for ordering, but it is not a device/configuration write.
_register_meter_tool(
    _UPDATE_TICKET_DEF,
    _update_ticket_handler,
    context_params=["conversation_id"],
    is_serial_only=True,
)
