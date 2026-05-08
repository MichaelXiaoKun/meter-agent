"""
Apply pipe configuration to a meter over MQTT (transport processor).

Publishes 50-W **smp** or non–50-W **spm**/**spd**/**spt**, then **ssa** using **ssa_code**
from ``processors.transducer_angle.resolve_transducer_angle``. Subscribes to meter/pub
and verifies pipe fields (not angle).

``subscribe`` uses **QoS 0**; all ``publish`` calls use **QoS 1** (at-least-once).

Set **BLUEBOT_MQTT_DEBUG=1** (or ``true``/``on``) to print step-by-step traces to **stderr**.
When the agent runs under uvicorn, set this in the **orchestrator** environment so the subprocess
inherits stderr and ``[bluebot-mqtt]`` lines appear in the same terminal as the server.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt

_MQTT_SUBSCRIBE_QOS = 0
_MQTT_PUBLISH_QOS = 1


def _mqtt_trace_enabled() -> bool:
    return os.environ.get("BLUEBOT_MQTT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _mqtt_trace(msg: str) -> None:
    if _mqtt_trace_enabled():
        print(f"[bluebot-mqtt] {msg}", file=sys.stderr, flush=True)


try:
    _CallbackAPIVersion = mqtt.CallbackAPIVersion  # type: ignore[attr-defined]
except AttributeError:  # paho-mqtt v1.x
    _CallbackAPIVersion = None


def _make_mqtt_client(client_id: str) -> mqtt.Client:
    """
    paho-mqtt v2 requires an explicit callback API version; v1 does not.
    Use VERSION1 callbacks: on_message(client, userdata, message).
    """
    if _CallbackAPIVersion is not None:
        return mqtt.Client(
            _CallbackAPIVersion.VERSION1,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
    return mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)


def _fmt_mm(value_mm: float) -> str:
    return f"{round(float(value_mm), 2):.6f}"


def _wait_seconds_after_publish(model: Optional[str], *, is_50w: bool) -> int:
    if is_50w:
        return int(os.environ.get("BLUEBOT_PIPE_SET_WAIT_50W_SEC", "20"))
    m = model or ""
    if re.search(r"100[-\s]?RF[-\s]?WAN", m, flags=re.I):
        return int(os.environ.get("BLUEBOT_PIPE_SET_WAIT_SLOW_SEC", "60"))
    return int(os.environ.get("BLUEBOT_PIPE_SET_WAIT_DEFAULT_SEC", "20"))


_DEFAULT_MQTT_HOST = "mqtt-prod.bluebot.com"
_DEFAULT_MQTT_PORT = 1883


def _mqtt_settings() -> Dict[str, Any]:
    """
    Defaults match prod: plain TCP MQTT on port 1883 (mqtt://mqtt-prod.bluebot.com:1883).

    Override with BLUEBOT_MQTT_HOST / BLUEBOT_MQTT_PORT / BLUEBOT_MQTT_USE_TLS when needed.
    """
    host = (os.environ.get("BLUEBOT_MQTT_HOST") or _DEFAULT_MQTT_HOST).strip()
    port_raw = os.environ.get("BLUEBOT_MQTT_PORT", "").strip()
    use_tls_raw = (os.environ.get("BLUEBOT_MQTT_USE_TLS") or "").strip().lower()
    username = (os.environ.get("BLUEBOT_MQTT_USERNAME") or "").strip() or None
    password = (os.environ.get("BLUEBOT_MQTT_PASSWORD") or "").strip() or None

    port = int(port_raw) if port_raw else _DEFAULT_MQTT_PORT

    if use_tls_raw in ("1", "true", "yes", "on"):
        use_tls = True
    elif use_tls_raw in ("0", "false", "no", "off"):
        use_tls = False
    else:
        use_tls = port == 8883

    return {
        "host": host,
        "port": port,
        "use_tls": use_tls,
        "username": username,
        "password": password,
        "keepalive": int(os.environ.get("BLUEBOT_MQTT_KEEPALIVE", "60")),
        "verify_pause_sec": float(os.environ.get("BLUEBOT_MQTT_VERIFY_PAUSE_SEC", "2")),
    }


def _telemetry_suggests_success(
    messages: List[Dict[str, Any]],
    *,
    standard_index: str,
    outer_mm: float,
    wall_mm: float,
) -> Tuple[bool, str]:
    outer_s = _fmt_mm(outer_mm)
    wall_s = _fmt_mm(wall_mm)
    hay: List[str] = []
    for m in messages:
        payload = m.get("payload")
        if isinstance(payload, (dict, list)):
            hay.append(json.dumps(payload, separators=(",", ":"), default=str))
        else:
            hay.append(str(m.get("payload_raw", "")))

    blob = "\n".join(hay)
    if standard_index not in blob:
        return False, "Did not observe standard index echo in subscribed telemetry yet."
    if outer_s not in blob and f'"{outer_s}"' not in blob:
        return False, "Did not observe outer diameter echo in subscribed telemetry yet."
    if wall_s not in blob and f'"{wall_s}"' not in blob:
        return False, "Did not observe wall thickness echo in subscribed telemetry yet."

    return True, "Observed expected pipe fields in telemetry JSON (best-effort string match)."


def _wait_ssa_only_seconds(model: Optional[str]) -> int:
    """Post-**ssa** wait for angle-only publishes (no pipe field steps)."""
    m = model or ""
    if re.search(r"100[-\s]?RF[-\s]?WAN", m, flags=re.I):
        return int(os.environ.get("BLUEBOT_SSA_ONLY_WAIT_SLOW_SEC", "60"))
    return int(os.environ.get("BLUEBOT_SSA_ONLY_WAIT_SEC", "20"))


def _telemetry_suggests_ssa(messages: List[Dict[str, Any]], ssa_code: str) -> Tuple[bool, str]:
    hay: List[str] = []
    for m in messages:
        payload = m.get("payload")
        if isinstance(payload, (dict, list)):
            hay.append(json.dumps(payload, separators=(",", ":"), default=str))
        else:
            hay.append(str(m.get("payload_raw", "")))
    blob = "\n".join(hay)
    code = str(ssa_code).strip()
    if not code:
        return False, "Empty ssa_code."
    if code in blob and ("ssa" in blob.lower() or '"ssa"' in blob):
        return True, "Observed ssa-related telemetry (best-effort match)."
    if code in blob:
        return True, "Observed SSA code in subscribed telemetry (best-effort match)."
    return False, "Did not observe SSA code echo in subscribed telemetry yet."


def _telemetry_suggests_szv(messages: List[Dict[str, Any]]) -> Tuple[bool, str]:
    hay: List[str] = []
    for m in messages:
        payload = m.get("payload")
        if isinstance(payload, (dict, list)):
            hay.append(json.dumps(payload, separators=(",", ":"), default=str))
        else:
            hay.append(str(m.get("payload_raw", "")))
    blob = "\n".join(hay)
    if '"szv":"null"' in blob or "'szv': 'null'" in blob or "szv" in blob.lower():
        return True, "Observed szv-related telemetry (best-effort match)."
    return False, "Did not observe szv echo in subscribed telemetry yet."


def apply_zero_point_over_mqtt(device_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Publish only ``{"szv": "null"}`` to meter/sub/<NUI>.

    This asks the meter to enter set-zero-point state. Safety gates live in the
    orchestrator before this function is ever called.
    """
    if device_context.get("error"):
        return {"error": "device_context has error; resolve device before MQTT."}

    nui = device_context.get("network_unique_identifier")
    model = device_context.get("model")
    if not nui:
        return {"error": "device_context missing network_unique_identifier."}

    cfg = _mqtt_settings()
    wait_s = _wait_ssa_only_seconds(str(model) if model is not None else None)

    pub_topic = f"meter/sub/{nui}"
    sub_topic = f"meter/pub/{nui}"

    received: List[Dict[str, Any]] = []
    recv_lock = threading.Lock()

    def on_message(_client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            item = {"topic": msg.topic, "payload": payload}
        except Exception:
            item = {
                "topic": msg.topic,
                "payload_raw": msg.payload.decode("utf-8", errors="replace"),
            }
        with recv_lock:
            received.append(item)
        if _mqtt_trace_enabled():
            if isinstance(item.get("payload"), dict):
                tail = json.dumps(item["payload"], separators=(",", ":"))[:280]
            else:
                tail = str(item.get("payload_raw", ""))[:280]
            _mqtt_trace(f"<- recv topic={msg.topic!r} payload={tail!r}")

    client_id = f"lens_{uuid.uuid4()}"
    _mqtt_trace(
        f"zero_point start client_id={client_id!r} host={cfg['host']!r}:{cfg['port']} "
        f"tls={cfg['use_tls']} pub={pub_topic!r} sub={sub_topic!r} wait_after_szv_s={wait_s}"
    )
    client = _make_mqtt_client(client_id)
    client.on_message = on_message

    if cfg["username"]:
        client.username_pw_set(cfg["username"], cfg["password"])
        _mqtt_trace("using MQTT username/password")

    if cfg["use_tls"]:
        tls_ctx = ssl.create_default_context()
        if os.environ.get("BLUEBOT_MQTT_TLS_INSECURE", "").strip().lower() in ("1", "true", "yes"):
            tls_ctx.check_hostname = False
            tls_ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(tls_ctx)
        _mqtt_trace("TLS enabled on client")

    client.connect(cfg["host"], int(cfg["port"]), keepalive=int(cfg["keepalive"]))
    _mqtt_trace("TCP connect returned, starting network loop")
    client.loop_start()
    try:
        client.subscribe(sub_topic, qos=_MQTT_SUBSCRIBE_QOS)
        _mqtt_trace(f"subscribed topic={sub_topic!r} qos={_MQTT_SUBSCRIBE_QOS}")
        time.sleep(0.25)
        _mqtt_trace("post-subscribe sleep 0.25s done")

        szv_body = {"szv": "null"}
        body_json = json.dumps(szv_body, separators=(",", ":"))
        _mqtt_trace(f"publish qos={_MQTT_PUBLISH_QOS} topic={pub_topic!r} body={body_json}")
        client.publish(pub_topic, body_json, qos=_MQTT_PUBLISH_QOS)
        publishes: List[Dict[str, Any]] = [{"topic": pub_topic, "payload": szv_body}]
        verify_sleep = max(wait_s, float(cfg["verify_pause_sec"]))
        _mqtt_trace(f"post-publish sleep {verify_sleep}s (zero_point verify window)")
        time.sleep(verify_sleep)

        with recv_lock:
            snapshot = list(received)

        _mqtt_trace(f"done: received_message_count={len(snapshot)}")
        ok, verify_note = _telemetry_suggests_szv(snapshot)
        _mqtt_trace(f"verification_ok={ok} note={verify_note!r}")

        return {
            "error": None,
            "mode": "zero_point",
            "mqtt_host": cfg["host"],
            "mqtt_port": int(cfg["port"]),
            "mqtt_use_tls": bool(cfg["use_tls"]),
            "mqtt_client_id": client_id,
            "publish_topic": pub_topic,
            "subscribe_topic": sub_topic,
            "wait_seconds_used": wait_s,
            "publishes": publishes,
            "received_message_count": len(snapshot),
            "received_sample": snapshot[:5],
            "verification_ok": ok,
            "verification_note": verify_note,
        }
    finally:
        try:
            _mqtt_trace("loop_stop + disconnect")
            client.loop_stop()
        finally:
            try:
                client.disconnect()
            except Exception:
                pass


def apply_ssa_only_over_mqtt(device_context: Dict[str, Any], ssa_code: str) -> Dict[str, Any]:
    """
    Publish only ``{"ssa": "<ssa_code>"}`` to meter/sub/<NUI> (no spm/spd/spt or smp).

    Args:
        device_context: Output of resolve_device_context_by_serial (error=null).
        ssa_code: From resolve_transducer_angle.
    """
    if device_context.get("error"):
        return {"error": "device_context has error; resolve device before MQTT."}

    nui = device_context.get("network_unique_identifier")
    model = device_context.get("model")
    if not nui:
        return {"error": "device_context missing network_unique_identifier."}

    angle_code = (ssa_code or "").strip()
    if not angle_code:
        return {"error": "ssa_code is empty."}

    cfg = _mqtt_settings()
    wait_s = _wait_ssa_only_seconds(str(model) if model is not None else None)

    pub_topic = f"meter/sub/{nui}"
    sub_topic = f"meter/pub/{nui}"

    received: List[Dict[str, Any]] = []
    recv_lock = threading.Lock()

    def on_message(_client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            item = {"topic": msg.topic, "payload": payload}
        except Exception:
            item = {
                "topic": msg.topic,
                "payload_raw": msg.payload.decode("utf-8", errors="replace"),
            }
        with recv_lock:
            received.append(item)
        if _mqtt_trace_enabled():
            if isinstance(item.get("payload"), dict):
                tail = json.dumps(item["payload"], separators=(",", ":"))[:280]
            else:
                tail = str(item.get("payload_raw", ""))[:280]
            _mqtt_trace(f"<- recv topic={msg.topic!r} payload={tail!r}")

    client_id = f"lens_{uuid.uuid4()}"
    _mqtt_trace(
        f"ssa_only start client_id={client_id!r} host={cfg['host']!r}:{cfg['port']} "
        f"tls={cfg['use_tls']} pub={pub_topic!r} sub={sub_topic!r} wait_after_ssa_s={wait_s}"
    )
    client = _make_mqtt_client(client_id)
    client.on_message = on_message

    if cfg["username"]:
        client.username_pw_set(cfg["username"], cfg["password"])
        _mqtt_trace("using MQTT username/password")

    if cfg["use_tls"]:
        tls_ctx = ssl.create_default_context()
        if os.environ.get("BLUEBOT_MQTT_TLS_INSECURE", "").strip().lower() in ("1", "true", "yes"):
            tls_ctx.check_hostname = False
            tls_ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(tls_ctx)
        _mqtt_trace("TLS enabled on client")

    client.connect(cfg["host"], int(cfg["port"]), keepalive=int(cfg["keepalive"]))
    _mqtt_trace("TCP connect returned, starting network loop")
    client.loop_start()
    try:
        client.subscribe(sub_topic, qos=_MQTT_SUBSCRIBE_QOS)
        _mqtt_trace(f"subscribed topic={sub_topic!r} qos={_MQTT_SUBSCRIBE_QOS}")
        time.sleep(0.25)
        _mqtt_trace("post-subscribe sleep 0.25s done")

        ssa_body = {"ssa": angle_code}
        body_json = json.dumps(ssa_body, separators=(",", ":"))
        _mqtt_trace(f"publish qos={_MQTT_PUBLISH_QOS} topic={pub_topic!r} body={body_json}")
        client.publish(pub_topic, body_json, qos=_MQTT_PUBLISH_QOS)
        publishes: List[Dict[str, Any]] = [{"topic": pub_topic, "payload": ssa_body}]
        verify_sleep = max(wait_s, float(cfg["verify_pause_sec"]))
        _mqtt_trace(f"post-publish sleep {verify_sleep}s (ssa_only verify window)")
        time.sleep(verify_sleep)

        with recv_lock:
            snapshot = list(received)

        _mqtt_trace(f"done: received_message_count={len(snapshot)}")
        ok, verify_note = _telemetry_suggests_ssa(snapshot, angle_code)
        _mqtt_trace(f"verification_ok={ok} note={verify_note!r}")

        return {
            "error": None,
            "mode": "ssa_only",
            "mqtt_host": cfg["host"],
            "mqtt_port": int(cfg["port"]),
            "mqtt_use_tls": bool(cfg["use_tls"]),
            "mqtt_client_id": client_id,
            "publish_topic": pub_topic,
            "subscribe_topic": sub_topic,
            "wait_seconds_used": wait_s,
            "publishes": publishes,
            "received_message_count": len(snapshot),
            "received_sample": snapshot[:5],
            "verification_ok": ok,
            "verification_note": verify_note,
        }
    finally:
        try:
            _mqtt_trace("loop_stop + disconnect")
            client.loop_stop()
        finally:
            try:
                client.disconnect()
            except Exception:
                pass


def apply_pipe_configuration_over_mqtt(
    pipe_resolution: Dict[str, Any],
    ssa_code: str,
) -> Dict[str, Any]:
    """
    Publish pipe configuration to meter/sub/<NUI> and verify on meter/pub/<NUI>.

    Args:
        pipe_resolution: Output dict from resolve_device_and_pipe_specs (must have error=None).
        ssa_code: Numeric string for MQTT payload **ssa** from resolve_transducer_angle (e.g. "2").
    """
    if pipe_resolution.get("error"):
        return {"error": "pipe_resolution has error set; resolve specs before MQTT."}

    nui = pipe_resolution.get("network_unique_identifier")
    model = pipe_resolution.get("model")
    is_50w = bool(pipe_resolution.get("is_50w"))
    std_index = str(pipe_resolution.get("standard_index"))
    outer_mm = float(pipe_resolution["outer_diameter_mm"])
    wall_mm = float(pipe_resolution["wall_thickness_mm"])

    if not nui:
        return {"error": "pipe_resolution missing network_unique_identifier."}

    angle_code = (ssa_code or "").strip()
    if not angle_code:
        return {"error": "ssa_code is empty; call resolve_transducer_angle first."}

    cfg = _mqtt_settings()

    wait_s = _wait_seconds_after_publish(str(model) if model is not None else None, is_50w=is_50w)

    pub_topic = f"meter/sub/{nui}"
    sub_topic = f"meter/pub/{nui}"

    received: List[Dict[str, Any]] = []
    recv_lock = threading.Lock()

    def on_message(_client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            item = {"topic": msg.topic, "payload": payload}
        except Exception:
            item = {
                "topic": msg.topic,
                "payload_raw": msg.payload.decode("utf-8", errors="replace"),
            }
        with recv_lock:
            received.append(item)
        if _mqtt_trace_enabled():
            if isinstance(item.get("payload"), dict):
                tail = json.dumps(item["payload"], separators=(",", ":"))[:280]
            else:
                tail = str(item.get("payload_raw", ""))[:280]
            _mqtt_trace(f"<- recv topic={msg.topic!r} payload={tail!r}")

    client_id = f"lens_{uuid.uuid4()}"
    _mqtt_trace(
        f"full_pipe start client_id={client_id!r} is_50w={is_50w} host={cfg['host']!r}:{cfg['port']} "
        f"tls={cfg['use_tls']} pub={pub_topic!r} sub={sub_topic!r} inter_step_wait_s={wait_s}"
    )
    client = _make_mqtt_client(client_id)
    client.on_message = on_message

    if cfg["username"]:
        client.username_pw_set(cfg["username"], cfg["password"])
        _mqtt_trace("using MQTT username/password")

    tls_ctx = None
    if cfg["use_tls"]:
        tls_ctx = ssl.create_default_context()
        if os.environ.get("BLUEBOT_MQTT_TLS_INSECURE", "").strip().lower() in ("1", "true", "yes"):
            tls_ctx.check_hostname = False
            tls_ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(tls_ctx)
        _mqtt_trace("TLS enabled on client")

    client.connect(cfg["host"], int(cfg["port"]), keepalive=int(cfg["keepalive"]))
    _mqtt_trace("TCP connect returned, starting network loop")
    client.loop_start()
    try:
        client.subscribe(sub_topic, qos=_MQTT_SUBSCRIBE_QOS)
        _mqtt_trace(f"subscribed topic={sub_topic!r} qos={_MQTT_SUBSCRIBE_QOS}")
        time.sleep(0.25)
        _mqtt_trace("post-subscribe sleep 0.25s done")

        publishes: List[Dict[str, Any]] = []

        if is_50w:
            # 50-W shortcut: one publish, same shape as
            # {"smp":{"pm":"<pipeMaterialIndex>","pod":"<outerMm>","pwt":"<wallMm>"}}.
            # `pm` is the firmware pipe-material index from management standard (not physical serial).
            body = {
                "smp": {
                    "pm": str(std_index),
                    "pod": _fmt_mm(outer_mm),
                    "pwt": _fmt_mm(wall_mm),
                }
            }
            bj = json.dumps(body, separators=(",", ":"))
            _mqtt_trace(f"publish (50-W smp) qos={_MQTT_PUBLISH_QOS} topic={pub_topic!r} body={bj}")
            client.publish(pub_topic, bj, qos=_MQTT_PUBLISH_QOS)
            publishes.append({"topic": pub_topic, "payload": body})
            _mqtt_trace(f"sleep {wait_s}s after smp")
            time.sleep(wait_s)
        else:
            steps = [
                {"spm": str(std_index)},
                {"spd": _fmt_mm(outer_mm)},
                {"spt": _fmt_mm(wall_mm)},
            ]
            for i, body in enumerate(steps, start=1):
                bj = json.dumps(body, separators=(",", ":"))
                _mqtt_trace(f"publish step {i}/3 qos={_MQTT_PUBLISH_QOS} topic={pub_topic!r} body={bj}")
                client.publish(pub_topic, bj, qos=_MQTT_PUBLISH_QOS)
                publishes.append({"topic": pub_topic, "payload": body})
                _mqtt_trace(f"sleep {wait_s}s after step {i}")
                time.sleep(wait_s)

        ssa_body = {"ssa": angle_code}
        bj_ssa = json.dumps(ssa_body, separators=(",", ":"))
        _mqtt_trace(f"publish (ssa) qos={_MQTT_PUBLISH_QOS} topic={pub_topic!r} body={bj_ssa}")
        client.publish(pub_topic, bj_ssa, qos=_MQTT_PUBLISH_QOS)
        publishes.append({"topic": pub_topic, "payload": ssa_body})
        verify_sleep = max(wait_s, float(cfg["verify_pause_sec"]))
        _mqtt_trace(f"post-ssa sleep {verify_sleep}s (telemetry window)")
        time.sleep(verify_sleep)

        with recv_lock:
            snapshot = list(received)

        _mqtt_trace(f"done: received_message_count={len(snapshot)}")
        ok, verify_note = _telemetry_suggests_success(
            snapshot,
            standard_index=str(std_index),
            outer_mm=outer_mm,
            wall_mm=wall_mm,
        )
        _mqtt_trace(f"verification_ok={ok} note={verify_note!r}")

        return {
            "error": None,
            "mqtt_host": cfg["host"],
            "mqtt_port": int(cfg["port"]),
            "mqtt_use_tls": bool(cfg["use_tls"]),
            "mqtt_client_id": client_id,
            "publish_topic": pub_topic,
            "subscribe_topic": sub_topic,
            "wait_seconds_used": wait_s,
            "publishes": publishes,
            "received_message_count": len(snapshot),
            "received_sample": snapshot[:5],
            "verification_ok": ok,
            "verification_note": verify_note,
        }
    finally:
        try:
            _mqtt_trace("loop_stop + disconnect")
            client.loop_stop()
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
