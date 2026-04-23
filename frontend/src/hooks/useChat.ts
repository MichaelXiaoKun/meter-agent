import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import type { Message, PlotAttachment, PlotSummary, SSEEvent } from "../types";
import * as api from "../api";
import {
  applyThinkingElapsed,
  reduceTurnActivity,
  type TurnActivityStep,
} from "../turnActivity";

function isAbortOrUnload(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof TypeError && /load failed/i.test(err.message)) return true;
  return false;
}

export type AgentStatus =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "thinking" }
  | { kind: "queued"; message: string }
  | { kind: "streaming" }
  | { kind: "tool_call"; tool: string }
  | { kind: "tool_progress"; tool: string; message: string }
  | { kind: "tool_result"; tool: string; success: boolean }
  | { kind: "compressing" }
  | { kind: "error"; error: string };

const IDLE: AgentStatus = { kind: "idle" };

const CTX_USAGE_KEY = (conversationId: string) =>
  `bb_ctx_usage_${conversationId}`;

function readStoredTokenUsage(
  conversationId: string | null
): { tokens: number; pct: number } {
  if (!conversationId || typeof sessionStorage === "undefined") {
    return { tokens: 0, pct: 0 };
  }
  try {
    const raw = sessionStorage.getItem(CTX_USAGE_KEY(conversationId));
    if (!raw) return { tokens: 0, pct: 0 };
    const p = JSON.parse(raw) as { tokens?: unknown; pct?: unknown };
    const tokens = typeof p.tokens === "number" ? p.tokens : 0;
    const pct = typeof p.pct === "number" ? p.pct : 0;
    return { tokens, pct };
  } catch {
    return { tokens: 0, pct: 0 };
  }
}

/**
 * Drop SSE events from another turn (client sets expected id before fetch) or duplicate seq.
 * Events without turn_id still apply (older servers).
 */
function shouldApplySseEvent(
  event: SSEEvent,
  expectedTurnId: MutableRefObject<string | null>,
  lastSeq: MutableRefObject<number>
): boolean {
  const tid = event.turn_id;
  if (
    expectedTurnId.current &&
    tid != null &&
    tid !== "" &&
    tid !== expectedTurnId.current
  ) {
    return false;
  }
  if (typeof event.seq === "number" && !Number.isNaN(event.seq)) {
    if (event.seq <= lastSeq.current) {
      return false;
    }
    lastSeq.current = event.seq;
  }
  return true;
}

export function useChat(
  activeConvId: string | null,
  token: string,
  anthropicApiKey?: string | null,
  /**
   * Optional Claude model ID from the UI's picker. Read via a ref inside
   * ``sendMessage`` so changes mid-session (user picks a different model)
   * take effect on the very next send without re-binding the callback.
   */
  model?: string | null,
) {
  const [viewedMessages, setViewedMessages] = useState<Message[]>([]);
  const [processingConvId, setProcessingConvId] = useState<string | null>(null);
  const [serverProcessing, setServerProcessing] = useState(false);
  const [streamStatus, setStreamStatus] = useState<AgentStatus>(IDLE);
  const [streamText, setStreamText] = useState("");
  const [streamPlots, setStreamPlots] = useState<PlotAttachment[]>([]);
  const [streamTokenUsage, setStreamTokenUsage] = useState(() =>
    readStoredTokenUsage(activeConvId)
  );
  /** Shown after a turn ends if Claude/API failed (SSE error or network). Cleared on new send / dismiss. */
  const [assistantError, setAssistantError] = useState<string | null>(null);
  /** True while loadMessages is in flight for activeConvId (avoids Welcome flash on conv switch). */
  const [historyLoading, setHistoryLoading] = useState(false);
  const [turnActivity, setTurnActivity] = useState<TurnActivityStep[]>([]);
  const processingMsgs = useRef<Message[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const activeConvRef = useRef(activeConvId);
  // Model is held in a ref so the picker can change mid-session without
  // rebinding sendMessage. Each send reads the latest value at fetch time.
  const modelRef = useRef<string | null>(model ?? null);
  const accumulatedRef = useRef("");
  const plotsRef = useRef<PlotAttachment[]>([]);
  /** Only clear the thread when switching to a different conversation — not on remount (Strict Mode) or re-fetch. */
  const prevLoadedConvIdRef = useRef<string | null>(null);
  /** UUID for this POST — must match every SSE event.turn_id (set before fetch). */
  const sseExpectedTurnIdRef = useRef<string | null>(null);
  const sseLastSeqRef = useRef<number>(0);
  const streamOpenedForTurnRef = useRef(false);
  /** First ``thinking`` in a segment; cleared when we show ``Thought for Ns``. */
  const thinkingSegmentStartMsRef = useRef<number | null>(null);

  useEffect(() => {
    activeConvRef.current = activeConvId;
  }, [activeConvId]);

  useEffect(() => {
    modelRef.current = model ?? null;
  }, [model]);

  // Restore context token snapshot after refresh / conv switch (sessionStorage per conversation).
  useLayoutEffect(() => {
    if (!activeConvId) {
      setStreamTokenUsage({ tokens: 0, pct: 0 });
      return;
    }
    setStreamTokenUsage(readStoredTokenUsage(activeConvId));
  }, [activeConvId]);

  // Persist last reported context size so the token UI survives page refresh.
  useEffect(() => {
    if (!activeConvId) return;
    if (streamTokenUsage.tokens <= 0 && streamTokenUsage.pct <= 0) {
      sessionStorage.removeItem(CTX_USAGE_KEY(activeConvId));
      return;
    }
    try {
      sessionStorage.setItem(
        CTX_USAGE_KEY(activeConvId),
        JSON.stringify(streamTokenUsage)
      );
    } catch {
      // ignore quota / private mode
    }
  }, [activeConvId, streamTokenUsage]);

  // Load from the server when activeConvId changes. Never wipe messages except on conv switch,
  // or a remount can clear the UI to [] while fetch is in flight (blank chat + no status).
  useEffect(() => {
    if (!activeConvId) {
      prevLoadedConvIdRef.current = null;
      setViewedMessages([]);
      setServerProcessing(false);
      setTurnActivity([]);
      setHistoryLoading(false);
      return;
    }

    const switched =
      prevLoadedConvIdRef.current !== null &&
      prevLoadedConvIdRef.current !== activeConvId;
    prevLoadedConvIdRef.current = activeConvId;

    if (switched) {
      setViewedMessages([]);
      setServerProcessing(false);
      setAssistantError(null);
      setTurnActivity([]);
    }

    const convId = activeConvId;
    setHistoryLoading(true);
    const ac = new AbortController();

    api.loadMessages(convId, ac.signal).then(async (msgs) => {
      // Ignore stale responses if user switched conversations before this completed.
      if (activeConvRef.current !== convId) return;
      setViewedMessages(msgs);
      setHistoryLoading(false);
      // If the last message is from the user, ask the server if it's still processing
      const last = msgs[msgs.length - 1];
      if (last?.role === "user" && typeof last.content === "string") {
        try {
          const active = await api.checkProcessing(convId, ac.signal);
          if (activeConvRef.current !== convId) return;
          setServerProcessing(active);
        } catch {
          // ignore — if the check fails, just don't show the banner
        }
      }
    }).catch((err) => {
      if (activeConvRef.current === convId) {
        setHistoryLoading(false);
      }
      if (!isAbortOrUnload(err))
        console.error("Failed to load messages:", err);
    });

    return () => ac.abort();
  }, [activeConvId]);

  // While streaming on the active conversation, mirror optimistic history from the ref
  useEffect(() => {
    if (!activeConvId || activeConvId !== processingConvId) return;
    setViewedMessages(processingMsgs.current);
    setServerProcessing(false);
  }, [activeConvId, processingConvId]);

  // Poll for completion while the server confirms it's actively processing
  useEffect(() => {
    if (!serverProcessing || !activeConvId || processingConvId) return;

    const convId = activeConvId;
    const ac = new AbortController();

    const interval = setInterval(async () => {
      try {
        const still = await api.checkProcessing(convId, ac.signal);
        if (!still) {
          clearInterval(interval);
          const msgs = await api.loadMessages(convId, ac.signal);
          if (activeConvRef.current === convId) {
            setViewedMessages(msgs);
          }
          setServerProcessing(false);
        }
      } catch {
        // ignore
      }
    }, 5000);

    return () => {
      ac.abort();
      clearInterval(interval);
    };
  }, [serverProcessing, activeConvId, processingConvId]);

  const sendMessage = useCallback(
    async (text: string, convIdOverride?: string) => {
      const convId = convIdOverride ?? activeConvId;
      if (!convId || !token || !text.trim()) return;

      if (processingConvId && processingConvId !== convId) {
        abortRef.current?.abort();
      }

      setProcessingConvId(convId);

      const userMsg: Message = { role: "user", content: text };
      setViewedMessages((prev) => {
        const next = [...prev, userMsg];
        processingMsgs.current = next;
        return next;
      });

      setStreamStatus({ kind: "connecting" });
      setStreamText("");
      setStreamPlots([]);
      setAssistantError(null);
      setTurnActivity([
        {
          seq: 0,
          kind: "connecting",
          title: "Sending your message",
          detail: undefined,
        },
      ]);
      streamOpenedForTurnRef.current = false;
      thinkingSegmentStartMsRef.current = null;
      accumulatedRef.current = "";
      plotsRef.current = [];
      sseLastSeqRef.current = 0;
      // Per-turn nonce for event dedup — server echoes it back on every
      // SSE/poll event so the client can filter stale ones. Must be
      // stable across this single ``sendMessage`` call and unique among
      // concurrent turns. Historically we used ``crypto.randomUUID``
      // with a ``turn-{ts}-{rand}`` fallback, but the fallback broke
      // event filtering on iOS < 15.4 (server used to reject the shape
      // and mint its own UUID, which then didn't match this ref).
      // Generate a UUIDv4 directly so the format is stable everywhere.
      const turnUuid = (() => {
        if (typeof crypto !== "undefined" && crypto.randomUUID) {
          return crypto.randomUUID();
        }
        // RFC4122 v4 shape with Math.random — cryptographic strength
        // isn't required; this is just a local nonce.
        const hex = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx";
        return hex.replace(/[xy]/g, (c) => {
          const r = (Math.random() * 16) | 0;
          const v = c === "x" ? r : (r & 0x3) | 0x8;
          return v.toString(16);
        });
      })();
      sseExpectedTurnIdRef.current = turnUuid;

      const controller = new AbortController();
      abortRef.current = controller;
      let streamReportedError = false;
      let streamErrorMessage: string | null = null;

      // ------------------------------------------------------------------
      // Transport selection: desktop streams via EventSource, phones
      // long-poll JSON. iOS WebKit + node-http-proxy + Wi-Fi coalescing
      // made the EventSource path arrive in one burst on phones no
      // matter how we padded / NODELAY'd, so for coarse-pointer clients
      // (touchscreens) we fall through to plain HTTP polling which is
      // immune to all of that.
      // ------------------------------------------------------------------
      const isCoarsePointer =
        typeof window !== "undefined" &&
        typeof window.matchMedia === "function" &&
        window.matchMedia("(pointer: coarse)").matches;
      const stream = isCoarsePointer ? api.streamChatViaPolling : api.streamChat;

      try {
        await stream(
          convId,
          text,
          token,
          (event: SSEEvent) => {
            if (!shouldApplySseEvent(event, sseExpectedTurnIdRef, sseLastSeqRef)) {
              return;
            }
            if (event.type === "thinking") {
              // Always reset: a new `thinking` SSE (e.g. after rate-limit) starts a fresh window.
              thinkingSegmentStartMsRef.current = Date.now();
            }
            setTurnActivity((prev) => {
              let next = reduceTurnActivity(prev, event, streamOpenedForTurnRef);
              // Do not end the segment on `compressing` — it can fire after `thinking` during
              // retries and would clear the timer before the first token/tool. ChatGPT-style
              // "Thought for" closes on first user-visible work only.
              const canCloseThinking =
                event.type === "text_delta" ||
                event.type === "text_stream" ||
                event.type === "tool_call" ||
                event.type === "error" ||
                event.type === "done";
              if (canCloseThinking) {
                const t0 = thinkingSegmentStartMsRef.current;
                const hasOpen = next.some(
                  (s) => s.kind === "thinking" && s.title === "Thinking"
                );
                if (hasOpen) {
                  const elapsedSec =
                    t0 != null
                      ? (Date.now() - t0) / 1000
                      : 0.1;
                  next = applyThinkingElapsed(
                    next,
                    t0 != null ? Math.max(0.05, elapsedSec) : 0.1
                  );
                  if (t0 != null) {
                    thinkingSegmentStartMsRef.current = null;
                  }
                }
              }
              return next;
            });
            switch (event.type) {
              case "queued":
                setStreamStatus({
                  kind: "queued",
                  message: event.message ?? "Waiting for a free slot…",
                });
                break;
              case "intent_route":
                setStreamStatus({ kind: "thinking" });
                break;
              case "thinking":
                accumulatedRef.current = "";
                setStreamStatus({ kind: "thinking" });
                setStreamText("");
                break;
              case "text_delta":
                accumulatedRef.current += event.text ?? "";
                setStreamStatus({ kind: "streaming" });
                setStreamText(accumulatedRef.current);
                break;
              case "tool_call":
                setStreamStatus({ kind: "tool_call", tool: event.tool ?? "" });
                break;
              case "tool_progress":
                setStreamStatus({
                  kind: "tool_progress",
                  tool: event.tool ?? "",
                  message: event.message ?? "Working…",
                });
                break;
              case "tool_result": {
                setStreamStatus({
                  kind: "tool_result",
                  tool: event.tool ?? "",
                  success: event.success ?? false,
                });
                const paths = event.plot_paths;
                if (paths?.length) {
                  const summaries = event.plot_summaries as PlotSummary[] | undefined;
                  const fallbackTz = event.plot_timezone;
                  const merged: PlotAttachment[] = paths.map((raw, i) => {
                    const filename = raw.split("/").pop() ?? raw;
                    const src = raw.startsWith("/api/") ? raw : `/api/plots/${filename}`;
                    const s =
                      summaries?.find((x) => x.filename === filename) ?? summaries?.[i];
                    return {
                      src,
                      title: s?.title,
                      plotTimezone: s?.plot_timezone ?? fallbackTz,
                      plotType: s?.plot_type,
                    };
                  });
                  plotsRef.current = [...plotsRef.current, ...merged];
                  setStreamPlots([...plotsRef.current]);
                }
                break;
              }
              case "token_usage": {
                const inputTokens = event.tokens ?? 0;
                setStreamTokenUsage({
                  tokens: inputTokens,
                  pct: event.pct ?? 0,
                });
                // First server event is often this — move off "Sending…" immediately.
                setStreamStatus((s) =>
                  s.kind === "connecting" ? { kind: "thinking" } : s
                );
                break;
              }
              case "compressing":
                setStreamStatus({ kind: "compressing" });
                break;
              case "error": {
                const msg = event.error ?? "Unknown error";
                streamReportedError = true;
                streamErrorMessage = msg;
                setAssistantError(msg);
                setStreamStatus({
                  kind: "error",
                  error: msg,
                });
                break;
              }
              case "done":
                break;
            }
          },
          controller.signal,
          turnUuid,
          anthropicApiKey,
          modelRef.current,
        );

        // Stream finished — load final persisted messages
        const final = await api.loadMessages(convId);
        processingMsgs.current = final;
        if (activeConvRef.current === convId) {
          setViewedMessages(final);
        }
        setStreamText("");
        setStreamPlots([]);
        setProcessingConvId(null);
        if (streamReportedError && streamErrorMessage) {
          setStreamStatus({ kind: "error", error: streamErrorMessage });
        } else {
          setStreamStatus(IDLE);
          setTurnActivity([]);
          streamOpenedForTurnRef.current = false;
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          const msg = (err as Error).message;
          setAssistantError(msg);
          setStreamStatus({
            kind: "error",
            error: msg,
          });
        }
        setProcessingConvId(null);
      } finally {
        abortRef.current = null;
      }
    },
    [activeConvId, token, processingConvId, anthropicApiKey]
  );

  const clearAssistantError = useCallback(() => {
    setAssistantError(null);
    setStreamStatus(IDLE);
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    sseExpectedTurnIdRef.current = null;
    sseLastSeqRef.current = 0;
    setAssistantError(null);
    setStreamStatus(IDLE);
    setStreamText("");
    setStreamPlots([]);
    setTurnActivity([]);
    streamOpenedForTurnRef.current = false;
    if (activeConvId) {
      try {
        sessionStorage.removeItem(CTX_USAGE_KEY(activeConvId));
      } catch {
        /* ignore */
      }
    }
    setStreamTokenUsage({ tokens: 0, pct: 0 });
    setProcessingConvId(null);
  }, [activeConvId]);

  const isViewingProcessing =
    !!processingConvId && activeConvId === processingConvId;

  const status: AgentStatus = isViewingProcessing
    ? streamStatus
    : assistantError
      ? { kind: "error", error: assistantError }
      : IDLE;

  return {
    messages: viewedMessages,
    status,
    streamingText: isViewingProcessing ? streamText : "",
    /** Last input-token count from the orchestrator (persists after a turn until switch/cancel). */
    tokenUsage: streamTokenUsage,
    historyLoading,
    pendingPlots: isViewingProcessing ? streamPlots : [],
    turnActivity,
    turnActivityActive: isViewingProcessing,
    processingConvId,
    serverProcessing,
    sendMessage,
    cancel,
    clearAssistantError,
  };
}
