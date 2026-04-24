import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import type { Message, PlotAttachment, PlotSummary, SSEEvent } from "../types";
import * as api from "../api";
import {
  applyThinkingElapsed,
  isInflightThinkingStep,
  reduceTurnActivity,
  type TurnActivityStep,
} from "../turnActivity";

function isAbortOrUnload(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof TypeError && /load failed/i.test(err.message)) return true;
  return false;
}

/**
 * Per-conversation streaming state — persists across conversation switches
 * so we can restore activity timeline / progress when user returns.
 */
interface ConvStreamingState {
  streamStatus: AgentStatus;
  streamLead: string;
  streamTail: string;
  streamPlots: PlotAttachment[];
  turnActivity: TurnActivityStep[];
  assistantError: string | null;
  // Accumulation refs for text chunks
  streamLeadAccRef: { current: string };
  streamTailAccRef: { current: string };
  sawToolCallRef: { current: boolean };
  plotsRef: { current: PlotAttachment[] };
  streamOpenedForTurnRef: { current: boolean };
  thinkingSegmentStartMsRef: { current: number | null };
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

const STREAMING_STATE_KEY = (conversationId: string) =>
  `bb_streaming_state_${conversationId}`;

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
 * Serialize streaming state for sessionStorage.
 * Omits refs that hold accumulated state during streaming.
 */
function serializeStreamingState(state: ConvStreamingState): string {
  return JSON.stringify({
    streamStatus: state.streamStatus,
    streamLead: state.streamLeadAccRef.current,
    streamTail: state.streamTailAccRef.current,
    streamPlots: state.plotsRef.current,
    turnActivity: state.turnActivity,
    assistantError: state.assistantError,
  });
}

/**
 * Deserialize streaming state from sessionStorage and restore refs.
 */
function deserializeStreamingState(json: string): ConvStreamingState {
  try {
    const data = JSON.parse(json);
    return {
      streamStatus: data.streamStatus ?? IDLE,
      streamLead: data.streamLead ?? "",
      streamTail: data.streamTail ?? "",
      streamPlots: data.streamPlots ?? [],
      turnActivity: data.turnActivity ?? [],
      assistantError: data.assistantError ?? null,
      streamLeadAccRef: { current: data.streamLead ?? "" },
      streamTailAccRef: { current: data.streamTail ?? "" },
      sawToolCallRef: { current: false },
      plotsRef: { current: data.streamPlots ?? [] },
      streamOpenedForTurnRef: { current: false },
      thinkingSegmentStartMsRef: { current: null },
    };
  } catch {
    return initConvStreamingState();
  }
}

function readStoredStreamingState(
  conversationId: string | null
): ConvStreamingState | null {
  if (!conversationId || typeof sessionStorage === "undefined") {
    return null;
  }
  try {
    const raw = sessionStorage.getItem(STREAMING_STATE_KEY(conversationId));
    if (!raw) return null;
    return deserializeStreamingState(raw);
  } catch {
    return null;
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

function initConvStreamingState(): ConvStreamingState {
  return {
    streamStatus: IDLE,
    streamLead: "",
    streamTail: "",
    streamPlots: [],
    turnActivity: [],
    assistantError: null,
    streamLeadAccRef: { current: "" },
    streamTailAccRef: { current: "" },
    sawToolCallRef: { current: false },
    plotsRef: { current: [] },
    streamOpenedForTurnRef: { current: false },
    thinkingSegmentStartMsRef: { current: null },
  };
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
  const [streamTokenUsage, setStreamTokenUsage] = useState(() =>
    readStoredTokenUsage(activeConvId)
  );
  /** True while loadMessages is in flight for activeConvId (avoids Welcome flash on conv switch). */
  const [historyLoading, setHistoryLoading] = useState(false);
  /** Per-conversation streaming state — preserved across conv switches. */
  const convStreamingStateMapRef = useRef<Map<string, ConvStreamingState>>(new Map());
  /** Trigger re-render when streaming state changes for the active conversation. */
  const [, setActiveConvStreamingState] = useState<ConvStreamingState | null>(null);

  const processingMsgs = useRef<Message[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const activeConvRef = useRef(activeConvId);
  // Model is held in a ref so the picker can change mid-session without
  // rebinding sendMessage. Each send reads the latest value at fetch time.
  const modelRef = useRef<string | null>(model ?? null);
  /** Only clear the thread when switching to a different conversation — not on remount (Strict Mode) or re-fetch. */
  const prevLoadedConvIdRef = useRef<string | null>(null);
  /** UUID for this POST — must match every SSE event.turn_id (set before fetch). */
  const sseExpectedTurnIdRef = useRef<string | null>(null);
  const sseLastSeqRef = useRef<number>(0);

  // Helper to get/create streaming state for a conversation
  // Attempts to restore from sessionStorage if not in memory (e.g., after page refresh)
  const getConvStreamingState = useCallback(
    (convId: string | null): ConvStreamingState | null => {
      if (!convId) return null;
      const map = convStreamingStateMapRef.current;
      if (!map.has(convId)) {
        // Try to restore from sessionStorage first (e.g., after page refresh)
        const stored = readStoredStreamingState(convId);
        if (stored) {
          map.set(convId, stored);
        } else {
          map.set(convId, initConvStreamingState());
        }
      }
      return map.get(convId) ?? null;
    },
    []
  );

  // Helper to update streaming state AND persist to sessionStorage
  const updateAndPersistStreamingState = useCallback(
    (convId: string | null) => {
      if (!convId) return;
      const state = getConvStreamingState(convId);
      if (!state) return;
      try {
        sessionStorage.setItem(STREAMING_STATE_KEY(convId), serializeStreamingState(state));
      } catch {
        // ignore quota / private mode
      }
      // Trigger re-render for active conversation
      if (convId === activeConvId) {
        setActiveConvStreamingState({ ...state });
      }
    },
    [activeConvId, getConvStreamingState]
  );

  // Get current streaming state for the active conversation
  const currentStreamingState = getConvStreamingState(activeConvId);

  useEffect(() => {
    activeConvRef.current = activeConvId;
  }, [activeConvId]);

  useEffect(() => {
    modelRef.current = model ?? null;
  }, [model]);

  // When active conversation changes, trigger re-render for new streaming state
  useEffect(() => {
    if (activeConvId) {
      const state = getConvStreamingState(activeConvId);
      setActiveConvStreamingState(state);
    } else {
      setActiveConvStreamingState(null);
    }
  }, [activeConvId, getConvStreamingState]);

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
          // If we have recovered streaming state from sessionStorage and server is still processing,
          // restore the processingConvId so the activity timeline shows
          if (active) {
            const recoveredState = getConvStreamingState(convId);
            if (recoveredState && (recoveredState.turnActivity.length > 0 || recoveredState.streamStatus.kind !== "idle")) {
              setProcessingConvId(convId);
            }
          }
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
  }, [activeConvId, getConvStreamingState]);

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

      const state = getConvStreamingState(convId);
      if (!state) return;

      // Reset streaming state for new turn
      state.streamStatus = { kind: "connecting" };
      state.streamLead = "";
      state.streamTail = "";
      state.streamPlots = [];
      state.assistantError = null;
      state.turnActivity = [
        {
          seq: 0,
          kind: "connecting",
          title: "Sending your message",
          detail: undefined,
        },
      ];
      state.streamLeadAccRef.current = "";
      state.streamTailAccRef.current = "";
      state.sawToolCallRef.current = false;
      state.streamOpenedForTurnRef.current = false;
      state.thinkingSegmentStartMsRef.current = null;
      state.plotsRef.current = [];
      sseLastSeqRef.current = 0;
      updateAndPersistStreamingState(convId);
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
            const state = getConvStreamingState(convId);
            if (!state) return;

            if (event.type === "thinking") {
              // Always reset: a new `thinking` SSE (e.g. after rate-limit) starts a fresh window.
              state.thinkingSegmentStartMsRef.current = Date.now();
            }

            // Update turn activity
            let next = reduceTurnActivity(state.turnActivity, event, state.streamOpenedForTurnRef);
            // Do not end the segment on `compressing` — it can fire after `thinking` during
            // retries and would clear the timer before the first token/tool. ChatGPT-style
            // "Thought for" closes on first user-visible work only.
            const canCloseThinking =
              event.type === "text_delta" ||
              event.type === "text_stream" ||
              event.type === "tool_call" ||
              event.type === "error" ||
              event.type === "tool_round_limit" ||
              event.type === "done";
            if (canCloseThinking) {
              const t0 = state.thinkingSegmentStartMsRef.current;
              const hasOpen = next.some(
                (step) => step != null && isInflightThinkingStep(step)
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
                  state.thinkingSegmentStartMsRef.current = null;
                }
              }
            }
            state.turnActivity = next;

            switch (event.type) {
              case "queued":
                state.streamStatus = {
                  kind: "queued",
                  message: event.message ?? "Waiting for a free slot…",
                };
                break;
              case "intent_route":
                state.streamStatus = { kind: "thinking" };
                break;
              case "thinking":
                // Server emits ``thinking`` before *every* Claude stream iteration. After the
                // first ``tool_call`` of this user turn, the next iteration streams the main
                // reply into ``tail`` — do not clear tail or reset ``sawToolCallRef`` here or
                // late tokens jump above the activity strip.
                if (!state.sawToolCallRef.current) {
                  state.streamLeadAccRef.current = "";
                  state.streamTailAccRef.current = "";
                  state.streamLead = "";
                  state.streamTail = "";
                }
                state.streamStatus = { kind: "thinking" };
                break;
              case "text_delta":
              case "text_stream": {
                const chunk = event.text ?? "";
                state.streamStatus = { kind: "streaming" };
                if (!state.sawToolCallRef.current) {
                  state.streamLeadAccRef.current += chunk;
                  state.streamLead = state.streamLeadAccRef.current;
                } else {
                  state.streamTailAccRef.current += chunk;
                  state.streamTail = state.streamTailAccRef.current;
                }
                break;
              }
              case "tool_call":
                state.sawToolCallRef.current = true;
                state.streamStatus = { kind: "tool_call", tool: event.tool ?? "" };
                break;
              case "tool_progress":
                state.streamStatus = {
                  kind: "tool_progress",
                  tool: event.tool ?? "",
                  message: event.message ?? "Working…",
                };
                break;
              case "tool_result": {
                state.streamStatus = {
                  kind: "tool_result",
                  tool: event.tool ?? "",
                  success: event.success ?? false,
                };
                const merged: PlotAttachment[] = [];
                // batch_analyze_flow: per-meter grouping via groupLabel.
                if (event.meters?.length) {
                  for (const meter of event.meters) {
                    const mPaths = meter.plot_paths ?? [];
                    if (!mPaths.length) continue;
                    const mSums = meter.plot_summaries;
                    const mTz = meter.plot_timezone;
                    for (let i = 0; i < mPaths.length; i++) {
                      const raw = mPaths[i];
                      const filename = raw.split("/").pop() ?? raw;
                      const src = raw.startsWith("/api/") ? raw : `/api/plots/${filename}`;
                      const s = mSums?.find((x) => x.filename === filename) ?? mSums?.[i];
                      merged.push({
                        src,
                        title: s?.title,
                        plotTimezone: s?.plot_timezone ?? mTz,
                        plotType: s?.plot_type,
                        groupLabel: meter.serial_number,
                      });
                    }
                  }
                } else {
                  // analyze_flow_data: flat paths.
                  const paths = event.plot_paths;
                  if (paths?.length) {
                    const summaries = event.plot_summaries as PlotSummary[] | undefined;
                    const fallbackTz = event.plot_timezone;
                    for (let i = 0; i < paths.length; i++) {
                      const raw = paths[i];
                      const filename = raw.split("/").pop() ?? raw;
                      const src = raw.startsWith("/api/") ? raw : `/api/plots/${filename}`;
                      const s = summaries?.find((x) => x.filename === filename) ?? summaries?.[i];
                      merged.push({
                        src,
                        title: s?.title,
                        plotTimezone: s?.plot_timezone ?? fallbackTz,
                        plotType: s?.plot_type,
                      });
                    }
                  }
                }
                if (merged.length) {
                  state.plotsRef.current = [...state.plotsRef.current, ...merged];
                  state.streamPlots = [...state.plotsRef.current];
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
                if (state.streamStatus.kind === "connecting") {
                  state.streamStatus = { kind: "thinking" };
                }
                break;
              }
              case "compressing":
                state.streamStatus = { kind: "compressing" };
                break;
              case "tool_round_limit": {
                const lim = event.limit ?? 0;
                const msg =
                  lim > 0
                    ? `Stopped after ${lim} assistant steps (safety limit).`
                    : "Stopped: assistant step limit reached.";
                state.streamStatus = { kind: "error", error: msg };
                break;
              }
              case "error": {
                const msg = event.error ?? "Unknown error";
                streamReportedError = true;
                streamErrorMessage = msg;
                state.assistantError = msg;
                state.streamStatus = {
                  kind: "error",
                  error: msg,
                };
                break;
              }
              case "done":
                break;
            }

            updateAndPersistStreamingState(convId);
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
        const state = getConvStreamingState(convId);
        if (state) {
          state.streamLead = "";
          state.streamTail = "";
          state.streamLeadAccRef.current = "";
          state.streamTailAccRef.current = "";
          state.sawToolCallRef.current = false;
          state.streamPlots = [];
          state.plotsRef.current = [];
          // Always drop the live strip so it never lingers under the persisted bubble
          // (``streamReportedError`` used to skip this and left Reasoning / Generating orphan rows).
          state.turnActivity = [];
          state.streamOpenedForTurnRef.current = false;
          if (streamReportedError && streamErrorMessage) {
            state.streamStatus = { kind: "error", error: streamErrorMessage };
          } else {
            state.streamStatus = IDLE;
          }
        }
        updateAndPersistStreamingState(convId);
        setProcessingConvId(null);
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          const msg = (err as Error).message;
          const state = getConvStreamingState(convId);
          if (state) {
            state.assistantError = msg;
            state.streamStatus = {
              kind: "error",
              error: msg,
            };
          }
        }
        const state = getConvStreamingState(convId);
        if (state) {
          state.streamLead = "";
          state.streamTail = "";
          state.streamLeadAccRef.current = "";
          state.streamTailAccRef.current = "";
          state.sawToolCallRef.current = false;
          state.streamPlots = [];
          state.plotsRef.current = [];
          state.turnActivity = [];
          state.streamOpenedForTurnRef.current = false;
        }
        updateAndPersistStreamingState(convId);
        setProcessingConvId(null);
      } finally {
        abortRef.current = null;
      }
    },
    [activeConvId, token, processingConvId, anthropicApiKey, getConvStreamingState, updateAndPersistStreamingState]
  );

  const clearAssistantError = useCallback(() => {
    if (activeConvId) {
      const state = getConvStreamingState(activeConvId);
      if (state) {
        state.assistantError = null;
        state.streamStatus = IDLE;
        updateAndPersistStreamingState(activeConvId);
      }
    }
  }, [activeConvId, getConvStreamingState, updateAndPersistStreamingState]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    sseExpectedTurnIdRef.current = null;
    sseLastSeqRef.current = 0;

    if (activeConvId) {
      const state = getConvStreamingState(activeConvId);
      if (state) {
        state.assistantError = null;
        state.streamStatus = IDLE;
        state.streamLead = "";
        state.streamTail = "";
        state.streamLeadAccRef.current = "";
        state.streamTailAccRef.current = "";
        state.sawToolCallRef.current = false;
        state.streamPlots = [];
        state.plotsRef.current = [];
        state.turnActivity = [];
        state.streamOpenedForTurnRef.current = false;
        updateAndPersistStreamingState(activeConvId);
      }
      try {
        sessionStorage.removeItem(CTX_USAGE_KEY(activeConvId));
        // Also clear streaming state from sessionStorage so it doesn't auto-restore on refresh
        sessionStorage.removeItem(STREAMING_STATE_KEY(activeConvId));
      } catch {
        /* ignore */
      }
    }
    setStreamTokenUsage({ tokens: 0, pct: 0 });
    setServerProcessing(false);
    setProcessingConvId(null);
  }, [activeConvId, getConvStreamingState, updateAndPersistStreamingState]);

  const isViewingProcessing =
    !!processingConvId && activeConvId === processingConvId;

  const status: AgentStatus = isViewingProcessing && currentStreamingState
    ? (currentStreamingState.streamStatus ?? IDLE)
    : currentStreamingState?.assistantError
      ? { kind: "error", error: currentStreamingState.assistantError }
      : IDLE;

  return {
    messages: viewedMessages,
    status,
    streamingLead: isViewingProcessing && currentStreamingState ? currentStreamingState.streamLead : "",
    streamingTail: isViewingProcessing && currentStreamingState ? currentStreamingState.streamTail : "",
    /** Last input-token count from the orchestrator (persists after a turn until switch/cancel). */
    tokenUsage: streamTokenUsage,
    historyLoading,
    pendingPlots: isViewingProcessing && currentStreamingState ? currentStreamingState.streamPlots : [],
    turnActivity: currentStreamingState?.turnActivity ?? [],
    turnActivityActive: isViewingProcessing,
    processingConvId,
    serverProcessing,
    sendMessage,
    cancel,
    clearAssistantError,
  };
}
