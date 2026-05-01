import type {
  Conversation,
  Message,
  SSEEvent,
  Ticket,
  TicketEvent,
  TicketOwnerType,
  TicketPriority,
  TicketStatus,
} from "./types";

const BASE = "/api";

/** Browser ``fetch`` errors when the orchestrator is down or /api is not proxied. */
function mapFetchNetworkError(e: unknown): string {
  const m = e instanceof Error ? e.message : String(e);
  const lower = m.toLowerCase();
  if (
    m === "Failed to fetch" ||
    m === "Load failed" ||
    lower.includes("networkerror") ||
    (lower.includes("network") && lower.includes("fetch")) ||
    (lower.includes("load") && lower.includes("failed"))
  ) {
    return (
      "Can’t reach the API server. If you are developing locally, start the meter orchestrator " +
      "(e.g. uvicorn on port 8000) and open the app through the Vite dev server so requests to /api are proxied."
    );
  }
  return m;
}

/** FastAPI may return ``detail`` as a string, or a validation error list. */
function parseFastApiDetail(
  body: unknown,
  fallback: string,
  status: number,
  statusText: string
): string {
  if (body && typeof body === "object" && "detail" in (body as object)) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string" && d.trim()) {
      return d;
    }
    if (Array.isArray(d) && d.length) {
      const first = d[0];
      if (typeof first === "string") {
        return first;
      }
      if (first && typeof first === "object" && "msg" in (first as object)) {
        const msg = (first as { msg: unknown }).msg;
        if (typeof msg === "string" && msg.trim()) {
          return msg;
        }
      }
    }
  }
  if (status === 502 || status === 503) {
    return "The service is temporarily unavailable. For local dev, run the orchestrator and use the Vite dev proxy for /api.";
  }
  if (status === 404) {
    return "This API was not found. Use a current orchestrator build that exposes POST /api/auth/forgot-password, or check your /api reverse proxy.";
  }
  if (statusText && statusText !== "Error" && statusText.length > 0) {
    return statusText;
  }
  return fallback;
}

function headers(
  token: string,
  opts?: { anthropicApiKey?: string | null }
): HeadersInit {
  const h: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
  const ak = opts?.anthropicApiKey?.trim();
  if (ak) {
    // X-LLM-Key is the generic override accepted by all providers.
    // X-Anthropic-Key is kept for backward compatibility with older backends.
    h["X-LLM-Key"] = ak;
    h["X-Anthropic-Key"] = ak;
  }
  return h;
}

// ---------------------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------------------

export async function login(
  username: string,
  password: string
): Promise<{ access_token: string; user: string }> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  } catch (e) {
    throw new Error(mapFetchNetworkError(e));
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(parseFastApiDetail(body, "Login failed", res.status, res.statusText));
  }
  return res.json();
}

/**
 * Triggers Auth0 database “change password” email (same as SaaS ``/forget-pass``), via orchestrator.
 */
export async function requestPasswordReset(email: string): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/auth/forgot-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
  } catch (e) {
    throw new Error(mapFetchNetworkError(e));
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      parseFastApiDetail(body, "Could not start password reset", res.status, res.statusText)
    );
  }
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

export async function listConversations(
  userId: string,
  signal?: AbortSignal
): Promise<Conversation[]> {
  const res = await fetch(
    `${BASE}/conversations?user_id=${encodeURIComponent(userId)}`,
    { signal }
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createConversation(
  userId: string,
  title = ""
): Promise<string> {
  const res = await fetch(`${BASE}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, title }),
  });
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  return data.id;
}

export async function loadMessages(
  convId: string,
  signal?: AbortSignal
): Promise<Message[]> {
  const res = await fetch(`${BASE}/conversations/${convId}/messages`, {
    signal,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface ProcessingStatus {
  processing: boolean;
  stream_id?: string;
  turn_id?: string;
  event_count?: number;
  done?: boolean;
}

export interface SalesLeadSummary {
  application?: string;
  industry?: string;
  site_count?: string;
  pipe_material?: string;
  pipe_size?: string;
  liquid?: string;
  expected_flow_range?: string;
  pipe_access?: string;
  installation_environment?: string;
  network_or_power?: string;
  reporting_goals?: string;
  timeline?: string;
  buyer_role?: string;
  contact?: string;
  notes?: string;
  [key: string]: unknown;
}

export interface SalesConversation {
  id: string;
  messages: Message[];
  lead_summary: SalesLeadSummary;
}

export interface SalesSSEEvent {
  type:
  | "text_delta"
  | "tool_call"
  | "tool_result"
  | "validation_start"
  | "validation_result"
  | "lead_summary"
  | "thinking"
  | "token_usage"
  | "queued"
  | "tool_round_limit"
  | "done"
  | "error";
  text?: string;
  tool?: string;
  input?: Record<string, unknown>;
  success?: boolean;
  lead_summary?: SalesLeadSummary;
  completion_score?: number;
  missing_fields?: string[];
  verdict?: string;
  next_action?: string;
  message?: string;
  error?: string;
  turn_id?: string;
  seq?: number;
}

export async function getProcessingStatus(
  convId: string,
  signal?: AbortSignal
): Promise<ProcessingStatus> {
  const res = await fetch(`${BASE}/conversations/${convId}/status`, { signal });
  if (!res.ok) return { processing: false };
  const data = await res.json();
  return {
    processing: !!data.processing,
    stream_id: typeof data.stream_id === "string" ? data.stream_id : undefined,
    turn_id: typeof data.turn_id === "string" ? data.turn_id : undefined,
    event_count: typeof data.event_count === "number" ? data.event_count : undefined,
    done: typeof data.done === "boolean" ? data.done : undefined,
  };
}

export async function checkProcessing(
  convId: string,
  signal?: AbortSignal
): Promise<boolean> {
  return (await getProcessingStatus(convId, signal)).processing;
}

// ---------------------------------------------------------------------------
// Native admin tickets
// ---------------------------------------------------------------------------

export interface TicketCreateInput {
  user_id: string;
  conversation_id?: string | null;
  serial_number?: string | null;
  title: string;
  description?: string;
  success_criteria: string;
  status?: TicketStatus;
  priority?: TicketPriority;
  owner_type?: TicketOwnerType;
  owner_id?: string | null;
  created_by_turn_id?: string | null;
  due_at?: number | null;
  metadata?: Record<string, unknown> | null;
}

export interface TicketUpdateInput {
  user_id: string;
  title?: string | null;
  description?: string | null;
  success_criteria?: string | null;
  status?: TicketStatus | null;
  priority?: TicketPriority | null;
  owner_type?: TicketOwnerType | null;
  owner_id?: string | null;
  due_at?: number | null;
  serial_number?: string | null;
  metadata?: Record<string, unknown> | null;
  note?: string;
  evidence?: Record<string, unknown> | null;
}

export interface TicketEventInput {
  user_id: string;
  event_type: string;
  actor_type?: string;
  actor_id?: string | null;
  note?: string;
  turn_id?: string | null;
  evidence?: Record<string, unknown> | null;
}

export async function listTickets(
  userId: string,
  opts: {
    conversationId?: string | null;
    serialNumber?: string | null;
    status?: TicketStatus | TicketStatus[];
    signal?: AbortSignal;
  } = {},
): Promise<Ticket[]> {
  const params = new URLSearchParams({ user_id: userId });
  if (opts.conversationId) params.set("conversation_id", opts.conversationId);
  if (opts.serialNumber) params.set("serial_number", opts.serialNumber);
  if (opts.status) {
    params.set(
      "status",
      Array.isArray(opts.status) ? opts.status.join(",") : opts.status,
    );
  }
  const res = await fetch(`${BASE}/tickets?${params.toString()}`, {
    signal: opts.signal,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<Ticket[]>;
}

export async function createTicket(
  body: TicketCreateInput,
  accessToken: string,
  signal?: AbortSignal,
): Promise<Ticket> {
  const res = await fetch(`${BASE}/tickets`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<Ticket>;
}

export async function updateTicket(
  ticketId: string,
  body: TicketUpdateInput,
  accessToken: string,
  signal?: AbortSignal,
): Promise<Ticket> {
  const res = await fetch(`${BASE}/tickets/${encodeURIComponent(ticketId)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<Ticket>;
}

export async function appendTicketEvent(
  ticketId: string,
  body: TicketEventInput,
  accessToken: string,
  signal?: AbortSignal,
): Promise<TicketEvent> {
  const res = await fetch(`${BASE}/tickets/${encodeURIComponent(ticketId)}/events`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<TicketEvent>;
}

// ---------------------------------------------------------------------------
// Public sales chat
// ---------------------------------------------------------------------------

export async function createSalesConversation(title = "Sales conversation"): Promise<string> {
  const res = await fetch(`${BASE}/public/sales/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  return data.id;
}

export async function listSalesConversations(
  ids: string[],
  signal?: AbortSignal,
): Promise<Conversation[]> {
  if (ids.length === 0) return [];
  const params = new URLSearchParams({ ids: ids.join(",") });
  const res = await fetch(`${BASE}/public/sales/conversations?${params}`, { signal });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function loadSalesConversation(
  convId: string,
  signal?: AbortSignal,
): Promise<SalesConversation> {
  const res = await fetch(`${BASE}/public/sales/conversations/${convId}`, { signal });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getSalesProcessingStatus(
  convId: string,
  signal?: AbortSignal,
): Promise<ProcessingStatus> {
  const res = await fetch(
    `${BASE}/public/sales/conversations/${encodeURIComponent(convId)}/status`,
    { signal },
  );
  if (!res.ok) return { processing: false };
  const data = await res.json();
  return {
    processing: !!data.processing,
    stream_id: typeof data.stream_id === "string" ? data.stream_id : undefined,
    turn_id: typeof data.turn_id === "string" ? data.turn_id : undefined,
    event_count: typeof data.event_count === "number" ? data.event_count : undefined,
    done: typeof data.done === "boolean" ? data.done : undefined,
  };
}

export async function updateSalesConversationTitle(
  convId: string,
  title: string,
): Promise<void> {
  const res = await fetch(`${BASE}/public/sales/conversations/${convId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(await res.text());
}

export async function deleteSalesConversation(convId: string): Promise<void> {
  const res = await fetch(`${BASE}/public/sales/conversations/${convId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await res.text());
}

async function initSalesTurn(
  convId: string,
  message: string,
  signal?: AbortSignal,
  clientTurnId?: string,
): Promise<{ streamId: string; turnId?: string }> {
  const res = await fetch(`${BASE}/public/sales/conversations/${convId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      ...(clientTurnId ? { client_turn_id: clientTurnId } : {}),
    }),
    signal,
  });
  if (!res.ok) throw new Error(await res.text());
  const body = (await res.json()) as { stream_id?: string; turn_id?: string };
  if (!body.stream_id) throw new Error("Missing stream_id in sales chat response");
  return { streamId: body.stream_id, turnId: body.turn_id };
}

export async function pollSalesStream(
  streamId: string,
  onEvent: (event: SalesSSEEvent) => void,
  signal?: AbortSignal,
  startCursor = 0,
): Promise<void> {
  let cursor = Math.max(0, startCursor);
  while (true) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    const res = await fetch(
      `${BASE}/public/sales/streams/${streamId}/poll?cursor=${cursor}&wait_ms=1500&_=${Date.now()}`,
      {
        signal,
        cache: "no-store",
        headers: { "Cache-Control": "no-cache", Pragma: "no-cache" },
      },
    );
    if (!res.ok) throw new Error(await res.text());
    const body = (await res.json()) as {
      events?: SalesSSEEvent[];
      done?: boolean;
      next_cursor?: number;
    };
    const events = body.events ?? [];
    for (const event of events) onEvent(event);
    cursor = body.next_cursor ?? cursor + events.length;
    if (body.done) return;
    if (events.length === 0) await new Promise((r) => setTimeout(r, 50));
  }
}

export async function streamSalesChat(
  convId: string,
  message: string,
  onEvent: (event: SalesSSEEvent) => void,
  signal?: AbortSignal,
  clientTurnId?: string,
): Promise<void> {
  const { streamId } = await initSalesTurn(convId, message, signal, clientTurnId);
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("aborted", "AbortError"));
      return;
    }
    const es = new EventSource(`${BASE}/public/sales/streams/${streamId}`);
    let settled = false;
    let fallbackStarted = false;
    let lastSeq = 0;
    const settle = (fn: () => void) => {
      if (settled) return;
      settled = true;
      es.close();
      signal?.removeEventListener("abort", onAbort);
      fn();
    };
    const onAbort = () => settle(() => reject(new DOMException("aborted", "AbortError")));
    const startPollingFallback = () => {
      if (settled || fallbackStarted) return;
      fallbackStarted = true;
      es.close();
      pollSalesStream(streamId, onEvent, signal, lastSeq)
        .then(() => settle(() => resolve()))
        .catch((err) => settle(() => reject(err)));
    };
    signal?.addEventListener("abort", onAbort);
    es.onmessage = (ev: MessageEvent<string>) => {
      let parsed: SalesSSEEvent | null = null;
      try {
        parsed = JSON.parse(ev.data) as SalesSSEEvent;
      } catch {
        return;
      }
      if (typeof parsed.seq === "number") lastSeq = Math.max(lastSeq, parsed.seq);
      if (parsed.type === "done") {
        settle(() => resolve());
        return;
      }
      if (parsed.type === "error") {
        settle(() => reject(new Error(parsed.error || "Sales chat failed")));
        return;
      }
      onEvent(parsed);
    };
    es.onerror = () => startPollingFallback();
  });
}

export async function cancelSalesProcessing(
  convId: string,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/public/sales/conversations/${convId}/cancel`, {
    method: "POST",
    signal,
  });
  if (!res.ok) throw new Error(await res.text());
}

export async function cancelProcessing(
  convId: string,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${BASE}/conversations/${convId}/cancel`, {
    method: "POST",
    signal,
  });
  if (!res.ok) throw new Error(await res.text());
}

export async function deleteConversation(
  convId: string,
  userId: string
): Promise<void> {
  const res = await fetch(
    `${BASE}/conversations/${convId}?user_id=${encodeURIComponent(userId)}`,
    { method: "DELETE" }
  );
  if (!res.ok) throw new Error(await res.text());
}

export async function updateTitle(
  convId: string,
  title: string
): Promise<void> {
  const res = await fetch(`${BASE}/conversations/${convId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(await res.text());
}

/**
 * Kick off a chat turn and return its ``stream_id``.
 *
 * Split out so both the EventSource and polling transports below can
 * share it. Persists the user's message and starts the worker thread
 * server-side; the caller chooses how to consume the event log.
 */
async function initChatTurn(
  convId: string,
  message: string,
  token: string,
  signal?: AbortSignal,
  clientTurnId?: string,
  anthropicApiKey?: string | null,
  model?: string | null,
  confirmedActionId?: string | null,
  cancelledActionId?: string | null,
  supersededActionId?: string | null,
): Promise<{ streamId: string; turnId?: string }> {
  const clientTimezone =
    typeof Intl !== "undefined"
      ? Intl.DateTimeFormat().resolvedOptions().timeZone
      : undefined;
  const trimmedModel = model ? model.trim() : "";

  const initRes = await fetch(`${BASE}/conversations/${convId}/chat`, {
    method: "POST",
    headers: headers(token, { anthropicApiKey }),
    body: JSON.stringify({
      message,
      ...(clientTimezone ? { client_timezone: clientTimezone } : {}),
      ...(clientTurnId ? { client_turn_id: clientTurnId } : {}),
      ...(trimmedModel ? { model: trimmedModel } : {}),
      ...(confirmedActionId ? { confirmed_action_id: confirmedActionId } : {}),
      ...(cancelledActionId ? { cancelled_action_id: cancelledActionId } : {}),
      ...(supersededActionId ? { superseded_action_id: supersededActionId } : {}),
    }),
    signal,
  });
  if (!initRes.ok) throw new Error(await initRes.text());
  const initBody = (await initRes.json()) as {
    stream_id?: string;
    turn_id?: string;
  };
  const streamId = initBody.stream_id;
  if (!streamId) throw new Error("Missing stream_id in chat init response");
  return {
    streamId,
    turnId: typeof initBody.turn_id === "string" ? initBody.turn_id : undefined,
  };
}

export async function pollStream(
  streamId: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  startCursor = 0,
): Promise<void> {
  let cursor = Math.max(0, startCursor);
  const POLL_WAIT_MS = 1500;

  while (true) {
    if (signal?.aborted) {
      throw new DOMException("aborted", "AbortError");
    }

    // Cache-buster in the URL on top of ``cache: "no-store"`` — iOS
    // Safari has a long history of caching same-path GETs even with
    // varying query strings, and the only truly reliable way to
    // guarantee each poll hits the network is a unique URL. Cost is
    // negligible (~20 extra bytes per request).
    const url =
      `${BASE}/streams/${streamId}/poll` +
      `?cursor=${cursor}` +
      `&wait_ms=${POLL_WAIT_MS}` +
      `&_=${Date.now()}`;
    const res = await fetch(url, {
      signal,
      cache: "no-store",
      headers: { "Cache-Control": "no-cache", Pragma: "no-cache" },
    });
    if (!res.ok) {
      throw new Error(
        `Poll failed (${res.status}): ${await res.text().catch(() => res.statusText)}`,
      );
    }
    const body = (await res.json()) as {
      events?: SSEEvent[];
      done?: boolean;
      next_cursor?: number;
    };
    const events = body.events ?? [];
    for (const ev of events) {
      onEvent(ev);
    }
    cursor = body.next_cursor ?? cursor + events.length;
    if (body.done) return;

    // If the server returned no events (short-circuited with timeout),
    // add a tiny client-side delay so a pathologically fast server
    // can't spin us. Normally the server's long-poll absorbs the wait.
    if (events.length === 0) {
      await new Promise((r) => setTimeout(r, 50));
    }
  }
}

/**
 * Stream a chat reply over the desktop path: ``POST`` to create the
 * turn, then subscribe via native ``EventSource``. The native SSE
 * implementation parses events as bytes arrive and fires JS callbacks
 * per event boundary — on desktop WebKit/Blink this means per-token
 * typing works reliably through the Vite proxy.
 *
 * **Mobile caveat**: iOS Safari's EventSource *also* works by the spec
 * but its Wi-Fi receive path aggressively coalesces small TCP segments,
 * which (combined with node-http-proxy buffering in Vite dev) made the
 * entire reply arrive in one burst at end-of-stream. For mobile we use
 * ``streamChatViaPolling`` below, which sidesteps all streaming
 * semantics by polling a JSON endpoint every ~200 ms.
 */
export async function streamChat(
  convId: string,
  message: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  clientTurnId?: string,
  anthropicApiKey?: string | null,
  model?: string | null,
  confirmedActionId?: string | null,
  cancelledActionId?: string | null,
  supersededActionId?: string | null,
): Promise<void> {
  const { streamId } = await initChatTurn(
    convId,
    message,
    token,
    signal,
    clientTurnId,
    anthropicApiKey,
    model,
    confirmedActionId,
    cancelledActionId,
    supersededActionId,
  );

  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("aborted", "AbortError"));
      return;
    }

    const es = new EventSource(`${BASE}/streams/${streamId}`);
    let settled = false;
    let fallbackStarted = false;
    let lastSeq = 0;
    /**
     * EventSource can fail mid-turn through the dev proxy or browser network
     * stack while the worker keeps running server-side. If that happens before
     * a terminal event, resume through the same append-only event log with
     * long-polling from the last seen sequence number.
     */
    const settle = (fn: () => void) => {
      if (settled) return;
      settled = true;
      es.close();
      signal?.removeEventListener("abort", onAbort);
      fn();
    };

    const onAbort = () => settle(() => reject(new DOMException("aborted", "AbortError")));
    signal?.addEventListener("abort", onAbort);

    const startPollingFallback = () => {
      if (settled || fallbackStarted) return;
      fallbackStarted = true;
      es.close();
      pollStream(streamId, onEvent, signal, lastSeq)
        .then(() => settle(() => resolve()))
        .catch((err) => settle(() => reject(err)));
    };

    es.onmessage = (ev: MessageEvent<string>) => {
      let parsed: SSEEvent | null = null;
      try {
        parsed = JSON.parse(ev.data) as SSEEvent;
      } catch {
        return;
      }
      if (typeof parsed.seq === "number" && Number.isFinite(parsed.seq)) {
        lastSeq = Math.max(lastSeq, parsed.seq);
      }
      onEvent(parsed);
      if (parsed.type === "done" || parsed.type === "error") {
        settle(() => resolve());
      }
    };

    es.onerror = () => {
      if (settled) return;
      startPollingFallback();
    };
  });
}

/**
 * Stream a chat reply over the **mobile** path: long-polling.
 *
 * Why: iOS WebKit's fetch buffer + ``EventSource`` Wi-Fi coalescing +
 * node-http-proxy in Vite dev made true streaming unreliable on phones
 * no matter how much we padded / flushed / disabled Nagle. Polling
 * sidesteps all of that — each request is a short-lived JSON response,
 * which every mobile browser handles perfectly.
 *
 * The server's ``/poll`` endpoint long-polls for up to ``wait_ms`` if
 * the event log is idle, so ~200 ms between client polls feels live
 * (each response arrives within a few hundred ms of the next event
 * server-side) without drowning the network in empty requests.
 */
export async function streamChatViaPolling(
  convId: string,
  message: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  clientTurnId?: string,
  anthropicApiKey?: string | null,
  model?: string | null,
  confirmedActionId?: string | null,
  cancelledActionId?: string | null,
  supersededActionId?: string | null,
): Promise<void> {
  const { streamId } = await initChatTurn(
    convId,
    message,
    token,
    signal,
    clientTurnId,
    anthropicApiKey,
    model,
    confirmedActionId,
    cancelledActionId,
    supersededActionId,
  );

  await pollStream(streamId, onEvent, signal);
}

// ---------------------------------------------------------------------------
// Authenticated artifacts
// ---------------------------------------------------------------------------

export async function downloadArtifact(
  url: string,
  filename: string,
  token: string,
  anthropicApiKey?: string | null,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      headers: headers(token, { anthropicApiKey }),
    });
  } catch (e) {
    throw new Error(mapFetchNetworkError(e));
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(
      parseFastApiDetail(
        body,
        `Could not download ${filename}`,
        res.status,
        res.statusText,
      ),
    );
  }
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

// ---------------------------------------------------------------------------
// Server tuning (public)
// ---------------------------------------------------------------------------

export interface OrchestratorModelOption {
  /** Model ID sent back to the server (e.g. "claude-haiku-4-5", "gpt-4o", "gemini-2.0-flash"). */
  id: string;
  /** Short human label for the picker (e.g. "Haiku 4.5", "GPT-4o"). */
  label: string;
  /** Provider name: "anthropic" | "openai" | "gemini". */
  provider: string;
  /** Coarse tier bucket: "fast" | "balanced" | "max" | "reasoning" | "custom". */
  tier: string;
  /** One-line description shown on hover / inside the dropdown. */
  description: string;
  /** Per-model TPM guide for the UI's rate-limit bar when this model is selected. */
  tpm_input_guide_tokens: number;
  /** True for the model the server falls back to when no pick is sent. */
  is_default: boolean;
}

export interface OrchestratorConfig {
  tpm_input_guide_tokens: number;
  /** Input token count before run_turn compresses (TPM headroom; shown as main context bar). */
  max_input_tokens_target: number;
  /** Full model context window in tokens (informational). */
  model_context_window: number;
  tpm_headroom_fraction: number;
  /** Sum of input tokens recorded from this API process in the last tpm_window_seconds. */
  tpm_sliding_input_tokens_60s: number;
  tpm_window_seconds: number;
  /** True if ANTHROPIC_API_KEY is set on the server. */
  anthropic_server_configured?: boolean;
  /** Server default model ID (used by the UI when no stored pick is available). */
  default_model?: string;
  /** Allowlist the UI's model picker should render. */
  available_models?: OrchestratorModelOption[];
}

export async function fetchOrchestratorConfig(
  signal?: AbortSignal
): Promise<OrchestratorConfig> {
  const res = await fetch(`${BASE}/config`, { signal });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ---------------------------------------------------------------------------
// Public share links (snapshot, read-only)
// ---------------------------------------------------------------------------

export interface PublicShareToken {
  token: string;
  revokeKey?: string;
}

export async function createShare(
  convId: string,
  userId: string,
  accessToken: string,
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetch(`${BASE}/conversations/${encodeURIComponent(convId)}/share`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
    body: JSON.stringify({ user_id: userId }),
    signal,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `Share failed (${res.status})`);
  }
  const data = (await res.json()) as { token?: string };
  if (!data.token) throw new Error("Missing share token in response");
  return data.token;
}

export async function createSalesShare(
  convId: string,
  signal?: AbortSignal,
): Promise<PublicShareToken> {
  const res = await fetch(
    `${BASE}/public/sales/conversations/${encodeURIComponent(convId)}/share`,
    {
      method: "POST",
      signal,
    },
  );
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `Sales share failed (${res.status})`);
  }
  const data = (await res.json()) as { token?: string; revoke_key?: string };
  if (!data.token) throw new Error("Missing share token in response");
  return { token: data.token, revokeKey: data.revoke_key };
}

export async function revokeShare(
  shareToken: string,
  userId: string,
  accessToken: string,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(
    `${BASE}/shares/${encodeURIComponent(shareToken)}?user_id=${encodeURIComponent(userId)}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${accessToken}` },
      signal,
    },
  );
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `Revoke share failed (${res.status})`);
  }
}

export async function revokeSalesShare(
  shareToken: string,
  revokeKey?: string,
  signal?: AbortSignal,
): Promise<void> {
  if (!revokeKey) {
    throw new Error("Missing revoke key for this sales share link");
  }
  const res = await fetch(
    `${BASE}/public/sales/shares/${encodeURIComponent(shareToken)}?revoke_key=${encodeURIComponent(revokeKey)}`,
    {
      method: "DELETE",
      signal,
    },
  );
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `Revoke sales share failed (${res.status})`);
  }
}

export async function loadPublicShare(
  shareToken: string,
  signal?: AbortSignal,
): Promise<{ title: string; messages: Message[] }> {
  const res = await fetch(
    `${BASE}/public/shares/${encodeURIComponent(shareToken)}`,
    { signal },
  );
  if (!res.ok) {
    if (res.status === 404) {
      throw new Error("This share link is not available.");
    }
    throw new Error(await res.text().catch(() => res.statusText));
  }
  return res.json() as Promise<{ title: string; messages: Message[] }>;
}
