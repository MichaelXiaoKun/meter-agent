import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import type { Message, SSEEvent } from "../types";
import * as api from "../api";
import { reduceTurnActivity, type TurnActivityStep } from "../turnActivity";

function isAbortOrUnload(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof TypeError && /load failed/i.test(err.message)) return true;
  return false;
}

export type AgentStatus =
  | { kind: "idle" }
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
  anthropicApiKey?: string | null
) {
  const [viewedMessages, setViewedMessages] = useState<Message[]>([]);
  const [processingConvId, setProcessingConvId] = useState<string | null>(null);
  const [serverProcessing, setServerProcessing] = useState(false);
  const [streamStatus, setStreamStatus] = useState<AgentStatus>(IDLE);
  const [streamText, setStreamText] = useState("");
  const [streamPlots, setStreamPlots] = useState<string[]>([]);
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
  const accumulatedRef = useRef("");
  const plotsRef = useRef<string[]>([]);
  /** Only clear the thread when switching to a different conversation — not on remount (Strict Mode) or re-fetch. */
  const prevLoadedConvIdRef = useRef<string | null>(null);
  /** UUID for this POST — must match every SSE event.turn_id (set before fetch). */
  const sseExpectedTurnIdRef = useRef<string | null>(null);
  const sseLastSeqRef = useRef<number>(0);
  const streamOpenedForTurnRef = useRef(false);

  useEffect(() => {
    activeConvRef.current = activeConvId;
  }, [activeConvId]);

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

      setStreamStatus({ kind: "thinking" });
      setStreamText("");
      setStreamPlots([]);
      setAssistantError(null);
      setTurnActivity([]);
      streamOpenedForTurnRef.current = false;
      accumulatedRef.current = "";
      plotsRef.current = [];
      sseLastSeqRef.current = 0;
      const turnUuid =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `turn-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
      sseExpectedTurnIdRef.current = turnUuid;

      const controller = new AbortController();
      abortRef.current = controller;
      let streamReportedError = false;
      let streamErrorMessage: string | null = null;

      try {
        await api.streamChat(
          convId,
          text,
          token,
          (event: SSEEvent) => {
            if (!shouldApplySseEvent(event, sseExpectedTurnIdRef, sseLastSeqRef)) {
              return;
            }
            setTurnActivity((prev) =>
              reduceTurnActivity(prev, event, streamOpenedForTurnRef)
            );
            switch (event.type) {
              case "queued":
                setStreamStatus({
                  kind: "queued",
                  message: event.message ?? "Waiting for a free slot…",
                });
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
              case "tool_result":
                setStreamStatus({
                  kind: "tool_result",
                  tool: event.tool ?? "",
                  success: event.success ?? false,
                });
                if (event.plot_paths?.length) {
                  plotsRef.current = [...plotsRef.current, ...event.plot_paths];
                  setStreamPlots([...plotsRef.current]);
                }
                break;
              case "token_usage": {
                const inputTokens = event.tokens ?? 0;
                setStreamTokenUsage({
                  tokens: inputTokens,
                  pct: event.pct ?? 0,
                });
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
          anthropicApiKey
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
