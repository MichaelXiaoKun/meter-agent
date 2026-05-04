"""Public sales-chat routes."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from .. import app as app_runtime

router = APIRouter(tags=["sales-chat"])


@router.post("/api/public/sales/conversations")
def create_sales_conversation(body: app_runtime.SalesConversationRequest | None = None):
    """Create a public, pre-login sales conversation."""
    title = (body.title if body else "") or "Sales conversation"
    conv_id = app_runtime.store.create_sales_conversation(title[:80])
    return {"id": conv_id}


@router.get("/api/public/sales/conversations")
def list_sales_conversations(ids: str = Query(default="")):
    """List the browser-owned public sales conversations by explicit ids."""
    wanted = [part.strip() for part in ids.split(",") if part.strip()]
    return app_runtime.store.list_sales_conversations(wanted[:100])


@router.get("/api/public/sales/conversations/{conv_id}")
def get_sales_conversation(conv_id: str):
    """Load a public sales conversation without Auth0."""
    if not app_runtime.store.sales_conversation_exists(conv_id):
        raise HTTPException(404, "Sales conversation not found")
    return {
        "id": conv_id,
        "messages": app_runtime.store.load_sales_messages(conv_id),
        "lead_summary": app_runtime.store.load_sales_lead_summary(conv_id),
    }


@router.get("/api/public/sales/conversations/{conv_id}/status")
def sales_conversation_status(conv_id: str):
    """Check whether the server is actively processing this public sales conversation."""
    active_key = f"sales:{conv_id}"
    with app_runtime._streams_lock:
        stream_id = app_runtime._active_conversation_streams.get(active_key)
        stream = app_runtime._streams.get(stream_id) if stream_id else None
        if stream_id and stream is None:
            app_runtime._active_conversation_streams.pop(active_key, None)
            stream_id = None
        done = bool(stream.get("done")) if stream else False
        processing = active_key in app_runtime._active_conversations or (
            stream is not None and not done
        )
        body: dict[str, object] = {"processing": processing}
        if stream_id and stream is not None:
            body.update(
                {
                    "stream_id": stream_id,
                    "turn_id": stream.get("turn_id"),
                    "event_count": len(stream.get("events") or []),
                    "done": done,
                }
            )
        return body


@router.patch("/api/public/sales/conversations/{conv_id}")
def patch_sales_conversation(conv_id: str, body: app_runtime.UpdateTitleRequest):
    """Rename a public sales conversation."""
    if not app_runtime.store.sales_conversation_exists(conv_id):
        raise HTTPException(404, "Sales conversation not found")
    app_runtime.store.set_sales_title(conv_id, body.title[:120])
    return {"ok": True}


@router.delete("/api/public/sales/conversations/{conv_id}")
def delete_sales_conversation(conv_id: str):
    """Delete a public sales conversation."""
    if not app_runtime.store.sales_conversation_exists(conv_id):
        raise HTTPException(404, "Sales conversation not found")
    app_runtime.store.delete_sales_conversation(conv_id)
    return {"ok": True}


@router.post("/api/public/sales/conversations/{conv_id}/share")
def create_sales_conversation_share(conv_id: str):
    """Create a public read-only snapshot link for a sales conversation."""
    try:
        token, revoke_key = app_runtime.store.create_sales_share(conv_id)
    except LookupError as e:
        raise HTTPException(404, str(e) or "Sales conversation not found") from e
    except ValueError as e:
        raise HTTPException(400, str(e) or "Sales conversation cannot be shared") from e
    return {"token": token, "revoke_key": revoke_key}


@router.delete("/api/public/sales/shares/{token}")
def delete_sales_share(token: str, revoke_key: str = Query(...)):
    """Revoke a public sales share using the browser-held revoke key."""
    ok = app_runtime.store.revoke_sales_share(token, revoke_key)
    if not ok:
        raise HTTPException(404, "Share not found or access denied")
    return {"ok": True}


@router.post("/api/public/sales/conversations/{conv_id}/cancel")
def cancel_sales_processing(conv_id: str):
    """Request cancellation of an active public sales turn."""
    active_key = f"sales:{conv_id}"
    with app_runtime._streams_lock:
        cancel_event = app_runtime._cancel_events.get(active_key)
        is_active = (
            active_key in app_runtime._active_conversations
            or cancel_event is not None
            or active_key in app_runtime._active_conversation_streams
        )
        if is_active:
            app_runtime._active_conversations.discard(active_key)
            app_runtime._active_conversation_streams.pop(active_key, None)
            if cancel_event is not None:
                app_runtime._cancelled_conversations.add(active_key)
                cancel_event.set()
            else:
                app_runtime._cancelled_conversations.discard(active_key)
        else:
            app_runtime._cancelled_conversations.discard(active_key)
    return {
        "cancelled": cancel_event is not None
        or app_runtime.store.sales_conversation_exists(conv_id)
    }


@router.post("/api/public/sales/conversations/{conv_id}/chat")
async def sales_chat_init(conv_id: str, body: app_runtime.SalesChatRequest):
    """Persist a public sales message and start a sales-agent stream."""
    app_runtime._gc_streams()
    if not app_runtime.store.sales_conversation_exists(conv_id):
        raise HTTPException(404, "Sales conversation not found")
    messages = app_runtime.store.load_sales_messages(conv_id)
    checkpoint = len(messages)
    user_msg = {"role": "user", "content": body.message}
    messages.append(user_msg)
    app_runtime.store.append_sales_messages(conv_id, [user_msg])
    if checkpoint == 0:
        app_runtime.store.set_sales_title(conv_id, body.message[:60])

    def _turn_id_for_request() -> str:
        raw = (body.client_turn_id or "").strip()
        return raw or str(uuid.uuid4())

    turn_id = _turn_id_for_request()
    session_cond = threading.Condition()
    stream_id = str(uuid.uuid4())
    active_key = f"sales:{conv_id}"
    cancel_event = threading.Event()
    captured_events: list[dict] = []

    with app_runtime._streams_lock:
        app_runtime._streams[stream_id] = {
            "events": [],
            "done": False,
            "cond": session_cond,
            "created": time.monotonic(),
            "sse_consumed": False,
            "turn_id": turn_id,
            "conv_id": active_key,
            "kind": "sales",
        }
        app_runtime._active_conversations.add(active_key)
        app_runtime._active_conversation_streams[active_key] = stream_id
        app_runtime._cancel_events[active_key] = cancel_event

    def _append_event(event: dict) -> None:
        with session_cond:
            with app_runtime._streams_lock:
                sess = app_runtime._streams.get(stream_id)
                if sess is None:
                    return
                seq = len(sess["events"]) + 1
                sess["events"].append({**event, "turn_id": turn_id, "seq": seq})
            session_cond.notify_all()

    def _emit_event(event: dict) -> None:
        if cancel_event.is_set():
            raise app_runtime.TurnCancelledByUser("Turn cancelled by user")
        _append_event(event)
        with app_runtime._streams_lock:
            sess = app_runtime._streams.get(stream_id)
            if sess and sess.get("events"):
                captured_events.append(dict(sess["events"][-1]))

    def _mark_done() -> None:
        with session_cond:
            with app_runtime._streams_lock:
                sess = app_runtime._streams.get(stream_id)
                if sess is not None:
                    sess["done"] = True
            session_cond.notify_all()

    def _run() -> None:
        slot_acquired = False
        try:
            with app_runtime._streams_lock:
                if active_key in app_runtime._cancelled_conversations:
                    _append_event({"type": "done"})
                    return
            app_runtime.acquire_run_turn_slot(
                on_wait=lambda: _emit_event(
                    {
                        "type": "queued",
                        "message": (
                            "Waiting for a free slot — another chat turn is using the model. "
                            f"(limit {app_runtime.configured_max_slots()} concurrent turn(s) per server.)"
                        ),
                    }
                )
            )
            slot_acquired = True
            app_runtime.run_sales_turn(
                messages,
                conversation_id=conv_id,
                on_event=_emit_event,
            )
            new_tail = messages[checkpoint + 1 :]
            slim = app_runtime._slim_turn_events_for_history(captured_events, turn_id)
            if slim:
                slim.append(
                    {
                        "type": "done",
                        "turn_id": turn_id,
                        "seq": len(slim) + 1,
                    }
                )
                for msg in reversed(new_tail):
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        app_runtime.append_turn_activity_block(msg, slim)
                        break
            app_runtime.store.append_sales_messages(conv_id, new_tail)
            _emit_event(
                {
                    "type": "lead_summary",
                    "lead_summary": app_runtime.store.load_sales_lead_summary(conv_id),
                }
            )
            _emit_event({"type": "done"})
        except app_runtime.TurnCancelledByUser:
            app_runtime.logger.info("sales turn cancelled for conv %s", conv_id)
            _append_event({"type": "done"})
        except Exception as exc:
            app_runtime.logger.exception("sales turn failed for conv %s", conv_id)
            _append_event({"type": "error", "error": app_runtime._sse_error_message(exc)})
        finally:
            with app_runtime._streams_lock:
                app_runtime._cancelled_conversations.discard(active_key)
                app_runtime._cancel_events.pop(active_key, None)
                app_runtime._active_conversations.discard(active_key)
                if app_runtime._active_conversation_streams.get(active_key) == stream_id:
                    app_runtime._active_conversation_streams.pop(active_key, None)
            if slot_acquired:
                app_runtime.release_run_turn_slot()
            _mark_done()

    threading.Thread(target=_run, daemon=True).start()
    return {"stream_id": stream_id, "turn_id": turn_id}


@router.get("/api/public/sales/streams/{stream_id}")
async def sales_chat_stream(stream_id: str, request: Request):
    """SSE stream for a public sales chat session."""
    with app_runtime._streams_lock:
        meta = app_runtime._streams.get(stream_id)
        if meta is None or meta.get("kind") != "sales":
            raise HTTPException(404, detail="stream not found or expired")
        if meta["sse_consumed"]:
            raise HTTPException(409, detail="stream already consumed")
        meta["sse_consumed"] = True

    cond: threading.Condition = meta["cond"]

    def _snapshot(cursor: int) -> tuple[list[dict], bool, int]:
        with app_runtime._streams_lock:
            sess = app_runtime._streams.get(stream_id)
            if sess is None:
                return [], True, cursor
            events = sess["events"]
            total = len(events)
            return (events[cursor:] if cursor < total else []), bool(sess["done"]), total

    async def _stream() -> AsyncGenerator[dict, None]:
        cursor = 0
        yield {"comment": " " * 8192}
        while True:
            if await request.is_disconnected():
                return
            new_events, done_flag, total_len = _snapshot(cursor)
            for ev in new_events:
                yield {"data": json.dumps(dict(ev)), "comment": " " * 4096}
            cursor += len(new_events)
            if done_flag and cursor >= total_len:
                return

            def _wait() -> None:
                with cond:
                    cond.wait(timeout=0.25)

            await asyncio.to_thread(_wait)

    return EventSourceResponse(
        _stream(),
        ping=2,
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store, no-transform",
        },
    )


@router.get("/api/public/sales/streams/{stream_id}/poll")
async def sales_chat_stream_poll(
    stream_id: str,
    cursor: int = 0,
    wait_ms: int = 1000,
):
    """Long-poll fallback for public sales chat streams."""
    wait_ms = max(0, min(int(wait_ms), app_runtime._POLL_WAIT_MAX_MS))

    with app_runtime._streams_lock:
        meta = app_runtime._streams.get(stream_id)
        if meta is None or meta.get("kind") != "sales":
            raise HTTPException(404, detail="stream not found or expired")

    cond: threading.Condition = meta["cond"]

    def _snapshot() -> tuple[list[dict], bool, int]:
        with app_runtime._streams_lock:
            sess = app_runtime._streams.get(stream_id)
            if sess is None:
                return [], True, cursor
            events = sess["events"]
            done = bool(sess["done"])
            tail = events[cursor:] if cursor < len(events) else []
            return tail, done, len(events)

    events_out, done, length = _snapshot()
    if not events_out and not done and wait_ms > 0:
        def _wait_for_events() -> None:
            deadline = time.monotonic() + wait_ms / 1000.0
            with cond:
                while True:
                    with app_runtime._streams_lock:
                        sess = app_runtime._streams.get(stream_id)
                        if sess is None:
                            return
                        if len(sess["events"]) > cursor or sess["done"]:
                            return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                    cond.wait(timeout=min(remaining, 0.25))

        await asyncio.to_thread(_wait_for_events)
        events_out, done, length = _snapshot()

    return JSONResponse(
        {
            "events": [dict(ev) for ev in events_out],
            "done": done and length == cursor + len(events_out),
            "next_cursor": cursor + len(events_out),
        },
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )
