import type { Conversation, Message, SSEEvent } from "./types";

const BASE = "/api";

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
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? "Login failed");
  }
  return res.json();
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

export async function checkProcessing(
  convId: string,
  signal?: AbortSignal
): Promise<boolean> {
  const res = await fetch(`${BASE}/conversations/${convId}/status`, { signal });
  if (!res.ok) return false;
  const data = await res.json();
  return !!data.processing;
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

export async function streamChat(
  convId: string,
  message: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  clientTurnId?: string,
  anthropicApiKey?: string | null
): Promise<void> {
  const clientTimezone =
    typeof Intl !== "undefined"
      ? Intl.DateTimeFormat().resolvedOptions().timeZone
      : undefined;
  const res = await fetch(`${BASE}/conversations/${convId}/chat`, {
    method: "POST",
    headers: headers(token, { anthropicApiKey }),
    body: JSON.stringify({
      message,
      ...(clientTimezone ? { client_timezone: clientTimezone } : {}),
      ...(clientTurnId ? { client_turn_id: clientTurnId } : {}),
    }),
    signal,
  });
  if (!res.ok) throw new Error(await res.text());

  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  let currentEventType = "";
  const dispatchLine = (line: string) => {
    const trimmed = line.replace(/\r$/, "");
    if (trimmed.startsWith("event:")) {
      currentEventType = trimmed.slice(6).trim();
    } else if (trimmed.startsWith("data:")) {
      const data = trimmed.slice(5).trim();
      if (data) {
        try {
          const parsed: SSEEvent = JSON.parse(data);
          if (!parsed.type && currentEventType) {
            parsed.type = currentEventType as SSEEvent["type"];
          }
          onEvent(parsed);
        } catch {
          // skip malformed events
        }
      }
      currentEventType = "";
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: true });
    }
    if (done) {
      buffer += decoder.decode();
    }

    const lines = buffer.split("\n");
    if (done) {
      buffer = "";
      for (const line of lines) {
        if (line.length > 0) dispatchLine(line);
      }
      break;
    }

    buffer = lines.pop() ?? "";
    for (const line of lines) {
      dispatchLine(line);
    }
  }
}

// ---------------------------------------------------------------------------
// Server tuning (public)
// ---------------------------------------------------------------------------

export interface OrchestratorConfig {
  tpm_input_guide_tokens: number;
  /** Input token count before run_turn compresses (TPM headroom; shown as main context bar). */
  max_input_tokens_target: number;
  /** Full Claude Messages API context window (informational). */
  model_context_window: number;
  tpm_headroom_fraction: number;
  /** Sum of input tokens recorded from this API process in the last tpm_window_seconds. */
  tpm_sliding_input_tokens_60s: number;
  tpm_window_seconds: number;
  /** True if ANTHROPIC_API_KEY is set on the server (user may still pass X-Anthropic-Key). */
  anthropic_server_configured?: boolean;
}

export async function fetchOrchestratorConfig(
  signal?: AbortSignal
): Promise<OrchestratorConfig> {
  const res = await fetch(`${BASE}/config`, { signal });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
