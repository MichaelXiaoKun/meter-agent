"""Authenticated admin-chat routes."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from .. import app as app_runtime

router = APIRouter(tags=["admin-chat"])


@router.get("/api/conversations/{conv_id}/status")
def conversation_status(conv_id: str):
    """Check whether the server is actively processing this conversation."""
    with app_runtime._streams_lock:
        stream_id = app_runtime._active_conversation_streams.get(conv_id)
        stream = app_runtime._streams.get(stream_id) if stream_id else None
        if stream_id and stream is None:
            app_runtime._active_conversation_streams.pop(conv_id, None)
            stream_id = None
        done = bool(stream.get("done")) if stream else False
        processing = conv_id in app_runtime._active_conversations or (
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


@router.post("/api/conversations/{conv_id}/cancel")
def cancel_processing(conv_id: str):
    """Request cancellation of an active conversation turn.

    Sets an event flag that the worker thread checks periodically during
    tool execution and immediately removes the conversation from the active set
    so checkProcessing returns false. The cancellation is cooperative — the thread
    may take a few seconds to notice and stop if it's in the middle of a tool execution.
    """
    with app_runtime._streams_lock:
        cancel_event = app_runtime._cancel_events.get(conv_id)
        is_active = (
            conv_id in app_runtime._active_conversations
            or cancel_event is not None
            or conv_id in app_runtime._active_conversation_streams
        )
        if is_active:
            # Immediately remove from active so checkProcessing returns false
            # (the worker thread will clean up the finally block)
            app_runtime._active_conversations.discard(conv_id)
            app_runtime._active_conversation_streams.pop(conv_id, None)
            # Signal the worker thread to stop (if it has a cancel event)
            if cancel_event is not None:
                app_runtime._cancelled_conversations.add(conv_id)
                cancel_event.set()
            else:
                app_runtime._cancelled_conversations.discard(conv_id)
        else:
            app_runtime._cancelled_conversations.discard(conv_id)
    return {"cancelled": cancel_event is not None}


@router.post("/api/conversations/{conv_id}/chat")
async def chat_init(
    conv_id: str,
    body: app_runtime.ChatRequest,
    authorization: str = Header(...),
    x_anthropic_key: str | None = Header(default=None, alias="X-Anthropic-Key"),
    x_llm_key: str | None = Header(default=None, alias="X-LLM-Key"),
):
    """Persist the user's message, kick off the worker thread, and return a
    one-shot ``stream_id`` the browser can subscribe to via ``EventSource``.

    The actual SSE event stream lives at ``GET /api/streams/{stream_id}``
    (see :func:`chat_stream`).

    ``X-LLM-Key`` is the generic per-request provider key (works for any
    provider).  ``X-Anthropic-Key`` is accepted for backward compatibility
    and used only when ``X-LLM-Key`` is absent.
    """
    app_runtime._gc_streams()
    token = app_runtime._bearer_token(authorization)
    # X-LLM-Key takes priority; fall back to the legacy X-Anthropic-Key header.
    user_anthropic_key = (x_llm_key or x_anthropic_key or "").strip() or None
    messages = app_runtime.store.load_messages(conv_id)

    if isinstance(body.questionnaire_response, dict):
        user_content: str | list[dict] = [
            {"type": "text", "text": body.message},
            {
                **body.questionnaire_response,
                "type": "questionnaire_response",
            },
        ]
    else:
        user_content = body.message
    user_msg = {"role": "user", "content": user_content}
    messages.append(user_msg)
    # n_messages_after_user used to calculate how many DB messages the summary covers on compress.
    n_messages_after_user = len(messages)
    app_runtime.store.append_messages(conv_id, [user_msg])

    # Build api_messages from the cached context summary so we don't re-call the compression
    # model on every turn of a long conversation. messages (full history) stays untouched for
    # display and DB; api_messages is what we pass to Claude.
    context_summary, summary_covers = app_runtime.store.get_api_context_info(conv_id)
    if context_summary and 0 < summary_covers < len(messages):
        api_messages: list = [{"role": "user", "content": context_summary}] + messages[summary_covers:]
    else:
        api_messages = messages

    if len(messages) == 1:
        app_runtime.store.set_title(conv_id, body.message[:60])

    # One logical turn per POST: every event gets the same ``turn_id`` and
    # a monotonic ``seq`` so the client can ignore stale / out-of-order
    # events (abort, double fire, reconnect edge cases).
    #
    # IMPORTANT: we echo the client's ``client_turn_id`` *verbatim*.
    # Previously we parsed it as a UUID and generated a fresh one on
    # ``ValueError`` — but iOS Safari < 15.4 has no ``crypto.randomUUID``
    # and the JS fallback generated an id shaped like ``turn-1234-abc``
    # which isn't a valid UUID. The server then minted its own random
    # UUID, every SSE event carried that UUID, the client's
    # ``sseExpectedTurnIdRef`` kept the original string, and
    # ``shouldApplySseEvent`` silently dropped every event. The UI sat
    # on "Preparing reply…" forever because state never advanced.
    #
    # The turn_id is just a nonce for client-side dedup across
    # re-renders / aborts. It doesn't need to be a UUID — it only needs
    # to round-trip unchanged.
    def _turn_id_for_request() -> str:
        raw = (body.client_turn_id or "").strip()
        return raw or str(uuid.uuid4())

    turn_id = _turn_id_for_request()

    # ------------------------------------------------------------------
    # Shared session storage: event log + Condition for wakeup
    # ------------------------------------------------------------------
    # Events are appended to ``events`` (list of dicts) in monotonic
    # ``seq`` order. Consumers (EventSource or long-poll) read by
    # ``cursor`` index. The ``cond`` Condition is notified whenever new
    # events land so long-polling pollers can unblock immediately. A
    # single append-only log replaces the old per-session queue, which
    # means the session supports *either* streaming or polling (or even
    # both simultaneously — e.g. EventSource + a debug tab). This is
    # what makes the mobile polling fallback work without a second
    # worker thread.
    session_cond = threading.Condition()
    stream_id = str(uuid.uuid4())

    with app_runtime._streams_lock:
        app_runtime._streams[stream_id] = {
            "events": [],  # list[dict]; appended in seq order
            "done": False,  # True once worker drains
            "cond": session_cond,
            "created": time.monotonic(),
            "sse_consumed": False,  # SSE is single-shot; polling is not
            "turn_id": turn_id,
            "conv_id": conv_id,
        }
        # Mark active as soon as the turn is accepted, not only after the
        # worker acquires a run slot, so a refresh during queueing can resume.
        app_runtime._active_conversations.add(conv_id)
        app_runtime._active_conversation_streams[conv_id] = stream_id

    def _emit_event(event: dict) -> None:
        with session_cond:
            with app_runtime._streams_lock:
                sess = app_runtime._streams.get(stream_id)
                if sess is None:
                    return
                seq = len(sess["events"]) + 1
                sess["events"].append({**event, "turn_id": turn_id, "seq": seq})
            session_cond.notify_all()

    _TURN_PERSIST_KEYS = frozenset(
        {
            "type",
            "seq",
            "turn_id",
            "tool",
            "input",
            "success",
            "message",
            "tool_activity",
            "display_range",
            "report_truncated",
            "plot_timezone",
            "download_artifacts",
            "analysis_details",
            "meter_context",
            "diagnostic_summary",
            "config_workflow",
            "sweep_result",
            "ticket",
            "tickets",
            "verdict",
            "next_action",
            "tokens",
            "pct",
            "model",
            "intent",
            "source",
            "tools",
            "questionnaire",
            "pending",
            "rate_limit_wait_seconds",
            "current_tokens",
            "estimated_next_tokens",
            "tpm_limit",
            "tpm_cap",
            "overflow_tokens",
            "waited_seconds",
            "attempt",
            "text",  # unused on slim; kept for forward compat
            "limit",  # tool_round_limit
            "deduped",
        }
    )

    def _slim_turn_events_persisted(raw: list[dict], tid: str) -> list[dict]:
        """
        Coalesce token spam (text_delta) into a single text_stream marker for
        the UI to replay; drop oversized plot payloads (filenames only).
        """
        out: list[dict] = []
        for ev in raw:
            t = ev.get("type")
            if t == "text_delta":
                if not out or out[-1].get("type") != "text_stream":
                    out.append(
                        {
                            "type": "text_stream",
                            "turn_id": tid,
                            "seq": ev.get("seq", 0),
                        }
                    )
                continue
            s = {k: ev[k] for k in _TURN_PERSIST_KEYS if k in ev}
            s["type"] = t
            s.setdefault("turn_id", tid)
            s.setdefault("seq", ev.get("seq", 0))
            if t == "tool_result" and "plot_paths" in ev and ev.get("plot_paths"):
                s["plot_paths"] = [str(p).split("/")[-1] for p in (ev.get("plot_paths") or [])[:8]]
            if t == "tool_result" and "download_artifacts" in ev and ev.get("download_artifacts"):
                s["download_artifacts"] = [
                    a for a in (app_runtime._rewrite_download_artifacts({
                        "download_artifacts": ev.get("download_artifacts") or []
                    }).get("download_artifacts") or [])[:8]
                    if isinstance(a, dict)
                ]
            out.append(s)
        return out

    def _synthetic_context_summary_covers(msg: dict | None) -> bool:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            return False
        content = msg.get("content")
        return isinstance(content, str) and (
            content.startswith("[Context summary")
            or content.startswith("[Full thread compressed")
        )

    captured_events: list[dict] = []
    cancel_event = threading.Event()

    # Register cancel event for this conversation
    with app_runtime._streams_lock:
        app_runtime._cancel_events[conv_id] = cancel_event

    def _emit_event_with_capture(event: dict) -> None:
        # Check for cancellation on every event emission
        if cancel_event.is_set():
            raise app_runtime.TurnCancelledByUser("Turn cancelled by user")
        _emit_event(event)
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

    def _run():
        slot_acquired = False
        try:
            # Check if this conversation was already marked for cancellation
            with app_runtime._streams_lock:
                if conv_id in app_runtime._cancelled_conversations:
                    _emit_event({"type": "done"})
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
            # Surface progress before run_turn performs provider setup, rate-limit
            # discovery, or other network work that can otherwise leave the
            # client stuck on the optimistic "Sending" state with an empty stream.
            _emit_event_with_capture({"type": "thinking"})
            try:
                _, history_replaced = app_runtime.run_turn(
                    api_messages,
                    token,
                    on_event=_emit_event_with_capture,
                    client_timezone=body.client_timezone,
                    anthropic_api_key=user_anthropic_key,
                    model=body.model,
                    conversation_id=conv_id,
                    confirmed_action_id=body.confirmed_action_id,
                    cancelled_action_id=body.cancelled_action_id,
                    superseded_action_id=body.superseded_action_id,
                )
                slim = _slim_turn_events_persisted(captured_events, turn_id)
                if slim:
                    slim.append(
                        {
                            "type": "done",
                            "turn_id": turn_id,
                            "seq": len(slim) + 1,
                        }
                    )
                if (
                    api_messages
                    and api_messages[-1].get("role") == "assistant"
                    and slim
                ):
                    app_runtime.append_turn_activity_block(api_messages[-1], slim)
                # Locate user_msg by identity — normal layered compression keeps
                # it in the recent tail. The last-resort full-thread compression
                # intentionally replaces the whole API message list with a
                # synthetic summary, so the live stream can succeed even though
                # this identity anchor disappears. In that case, append
                # everything after the synthetic summary; otherwise the frontend
                # reloads history after ``done`` and the streamed response
                # appears to vanish.
                user_idx = next(
                    (i for i, m in enumerate(api_messages) if m is user_msg), None
                )
                summary_covers: int | None = None
                if user_idx is not None:
                    new_tail = api_messages[user_idx + 1:]
                    summary_covers = n_messages_after_user - user_idx
                elif _synthetic_context_summary_covers(
                    api_messages[0] if api_messages else None
                ):
                    new_tail = api_messages[1:]
                    summary_covers = n_messages_after_user
                else:
                    new_tail = []
                if history_replaced:
                    # Compression restructured api_messages for this API call.
                    # Update the cached summary so the next turn skips re-compression;
                    # original DB records are preserved — only the new reply is appended.
                    first_content = api_messages[0].get("content", "") if api_messages else ""
                    if (
                        isinstance(first_content, str)
                        and _synthetic_context_summary_covers(api_messages[0] if api_messages else None)
                        and summary_covers is not None
                    ):
                        app_runtime.store.set_api_context_info(conv_id, first_content, summary_covers)
                if not new_tail:
                    app_runtime.logger.warning(
                        "chat turn produced no new messages after user (conv=%s)", conv_id
                    )
                app_runtime.store.append_messages(conv_id, new_tail)
                app_runtime.update_title(conv_id, api_messages, anthropic_api_key=user_anthropic_key)
                _emit_event({"type": "done"})
            except app_runtime.TurnCancelledByUser:
                app_runtime.logger.info("chat turn cancelled for conv %s", conv_id)
                _emit_event({"type": "done"})
            except Exception as exc:
                app_runtime.logger.exception("run_turn failed for conv %s", conv_id)
                _emit_event({"type": "error", "error": app_runtime._sse_error_message(exc)})
        finally:
            # Clean up cancellation flag and event
            with app_runtime._streams_lock:
                app_runtime._cancelled_conversations.discard(conv_id)
                app_runtime._cancel_events.pop(conv_id, None)
                app_runtime._active_conversations.discard(conv_id)
                if app_runtime._active_conversation_streams.get(conv_id) == stream_id:
                    app_runtime._active_conversation_streams.pop(conv_id, None)
            if slot_acquired:
                app_runtime.release_run_turn_slot()
            _mark_done()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"stream_id": stream_id, "turn_id": turn_id}


@router.get("/api/streams/{stream_id}")
async def chat_stream(stream_id: str, request: Request):
    """SSE stream for a chat session previously started via POST.

    Designed for ``EventSource`` consumption on desktop browsers. Reads
    from the shared event log; a separate long-poll endpoint
    (``/api/streams/{id}/poll``) consumes the *same* log and is the
    fallback used by mobile browsers where EventSource has proven
    unreliable under iOS + Vite proxy buffering (no amount of padding or
    ``TCP_NODELAY`` tuning made it fully reliable).

    The SSE subscription is single-shot per session — React StrictMode's
    double render in dev would otherwise open two connections that split
    events between them.
    """
    with app_runtime._streams_lock:
        meta = app_runtime._streams.get(stream_id)
        if meta is None:
            raise HTTPException(404, detail="stream not found or expired")
        if meta["sse_consumed"]:
            raise HTTPException(409, detail="stream already consumed")
        meta["sse_consumed"] = True

    cond: threading.Condition = meta["cond"]

    def _snapshot(cursor: int) -> tuple[list[dict], bool, int]:
        """Return ``(new_events, done, total_len)`` for this cursor."""
        with app_runtime._streams_lock:
            sess = app_runtime._streams.get(stream_id)
            if sess is None:
                return [], True, cursor
            events = sess["events"]
            total = len(events)
            return (events[cursor:] if cursor < total else []), bool(sess["done"]), total

    async def _stream() -> AsyncGenerator[dict, None]:
        # Per-event SSE comment padding: each text_delta becomes a
        # >=12 KB SSE frame so iOS's Wi-Fi receive path doesn't coalesce
        # the small payload with later events. ``data`` + ``comment`` in
        # the same dict are encoded by sse-starlette into a single SSE
        # frame / ASGI send / TCP write, which was the specific thing
        # that made desktop per-token typing reliable over the Vite
        # proxy. (Mobile uses the polling fallback below instead.)
        _PRIMER = " " * 16384
        _PER_EVENT_PAD = " " * 12288
        cursor = 0
        yield {"comment": _PRIMER}
        while True:
            if await request.is_disconnected():
                return
            new_events, done_flag, total_len = _snapshot(cursor)
            for ev in new_events:
                yield {
                    "data": json.dumps(app_runtime._rewrite_artifact_urls(dict(ev))),
                    "comment": _PER_EVENT_PAD,
                }
            cursor += len(new_events)
            if done_flag and cursor >= total_len:
                return
            # Wait briefly for more events; 0.25 s keeps disconnect
            # detection responsive. ``cond.wait`` runs in a thread so we
            # don't block the event loop.
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


@router.get("/api/streams/{stream_id}/poll")
async def chat_stream_poll(
    stream_id: str,
    cursor: int = 0,
    wait_ms: int = 1000,
):
    """Return all events with ``seq > cursor``, optionally blocking briefly.

    Response shape::

        {"events": [<event>, ...], "done": true, "next_cursor": 42}

    ``next_cursor`` should be passed as ``cursor`` on the next request.
    ``done`` is ``true`` once the worker has emitted its terminal event
    *and* ``next_cursor`` equals the log length; at that point the
    client can stop polling.
    """
    wait_ms = max(0, min(int(wait_ms), app_runtime._POLL_WAIT_MAX_MS))

    with app_runtime._streams_lock:
        meta = app_runtime._streams.get(stream_id)
        if meta is None:
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
        # Long-poll: block briefly until new events arrive or the worker
        # finishes. We use ``asyncio.to_thread`` so we don't hold the
        # event loop while waiting on the condition variable.
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

    # ``Cache-Control: no-store`` is mandatory here: iOS Safari has been
    # observed to serve *repeat* poll responses from its in-memory cache
    # even when the query string (``cursor=N``) differs, if the path
    # matches a recent 200 OK and no explicit no-store was sent. That
    # silently breaks polling because the client keeps seeing the old
    # empty-events reply and never processes the events the server
    # actually emitted in between.
    body = {
        "events": [app_runtime._rewrite_artifact_urls(dict(ev)) for ev in events_out],
        "done": done and length == cursor + len(events_out),
        "next_cursor": cursor + len(events_out),
    }
    return JSONResponse(
        body,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )
