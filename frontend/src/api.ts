import type { Conversation, Message, SSEEvent } from "./types";

const BASE = "/api";

function headers(token: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
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
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${BASE}/conversations/${convId}/chat`, {
    method: "POST",
    headers: headers(token),
    body: JSON.stringify({ message }),
    signal,
  });
  if (!res.ok) throw new Error(await res.text());

  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    let currentEventType = "";
    for (const line of lines) {
      if (line.startsWith("event:")) {
        currentEventType = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const data = line.slice(5).trim();
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
    }
  }
}
