import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Message, SSEEvent } from "../types";
import * as api from "../api";
import {
  applyStreamEventToChatState,
  clearChatStreamStateAfterTurn,
  createChatStreamState,
  IDLE,
  resetChatStreamStateForTurn,
  type AgentStatus,
  type ChatStreamState,
} from "../chatStreamReducer";

export type { AgentStatus } from "../chatStreamReducer";

function isAbortOrUnload(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof TypeError && /load failed/i.test(err.message)) return true;
  return false;
}

type ConvStreamingState = ChatStreamState;
const initConvStreamingState = createChatStreamState;
const resetStreamingStateForTurn = resetChatStreamStateForTurn;
const clearStreamingStateAfterTurn = clearChatStreamStateAfterTurn;

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
    streamArtifacts: state.artifactsRef.current,
    turnActivity: state.turnActivity,
    workspaceEvents: state.workspaceEvents,
    assistantError: state.assistantError,
    streamId: state.streamId,
    turnId: state.turnId,
    cursor: state.cursor,
    sawToolCall: state.sawToolCallRef.current,
    streamOpened: state.streamOpenedForTurnRef.current,
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
      streamArtifacts: data.streamArtifacts ?? [],
      turnActivity: data.turnActivity ?? [],
      workspaceEvents: data.workspaceEvents ?? [],
      assistantError: data.assistantError ?? null,
      streamId: typeof data.streamId === "string" ? data.streamId : null,
      turnId: typeof data.turnId === "string" ? data.turnId : null,
      cursor: typeof data.cursor === "number" ? Math.max(0, data.cursor) : 0,
      streamLeadAccRef: { current: data.streamLead ?? "" },
      streamTailAccRef: { current: data.streamTail ?? "" },
      sawToolCallRef: { current: data.sawToolCall === true },
      plotsRef: { current: data.streamPlots ?? [] },
      artifactsRef: { current: data.streamArtifacts ?? [] },
      streamOpenedForTurnRef: { current: data.streamOpened === true },
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
    (convId: string | null, options?: { persist?: boolean }) => {
      if (!convId) return;
      const state = getConvStreamingState(convId);
      if (!state) return;
      try {
        if (options?.persist === false) {
          sessionStorage.removeItem(STREAMING_STATE_KEY(convId));
        } else {
          sessionStorage.setItem(STREAMING_STATE_KEY(convId), serializeStreamingState(state));
        }
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

  const applyStreamEvent = useCallback(
    (
      convId: string,
      event: SSEEvent,
    ): { applied: boolean; errorMessage?: string } => {
      const state = getConvStreamingState(convId);
      if (!state) return { applied: false };
      const result = applyStreamEventToChatState(
        state,
        event,
        {
          expectedTurnId: sseExpectedTurnIdRef,
          lastSeq: sseLastSeqRef,
        },
        { setTokenUsage: setStreamTokenUsage },
      );
      if (result.applied) {
        updateAndPersistStreamingState(convId);
      }
      return result;
    },
    [getConvStreamingState, updateAndPersistStreamingState]
  );

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
      processingMsgs.current = msgs;
      setViewedMessages(msgs);
      setHistoryLoading(false);
      // If the last message is from the user, ask the server if it's still processing
      const last = msgs[msgs.length - 1];
      if (last?.role === "user" && typeof last.content === "string") {
        try {
          const processing = await api.getProcessingStatus(convId, ac.signal);
          if (activeConvRef.current !== convId) return;
          if (!processing.processing) {
            setServerProcessing(false);
            return;
          }

          const recoveredState = getConvStreamingState(convId);
          if (!recoveredState) return;
          const streamId = processing.stream_id ?? recoveredState.streamId;
          const turnId = processing.turn_id ?? recoveredState.turnId;
          const sameStoredRun =
            !processing.stream_id ||
            recoveredState.streamId === processing.stream_id ||
            (turnId != null && recoveredState.turnId === turnId);
          const resumeCursor = sameStoredRun
            ? Math.max(0, recoveredState.cursor)
            : 0;

          if (
            !sameStoredRun ||
            (recoveredState.turnActivity.length === 0 &&
              recoveredState.streamStatus.kind === "idle")
          ) {
            resetStreamingStateForTurn(recoveredState, {
              status: streamId ? { kind: "connecting" } : { kind: "thinking" },
              streamId: streamId ?? null,
              turnId: turnId ?? null,
              cursor: resumeCursor,
              title: streamId
                ? "Reconnecting to current turn"
                : "Catching up with current turn",
            });
          } else {
            recoveredState.streamId = streamId ?? null;
            recoveredState.turnId = turnId ?? null;
            recoveredState.cursor = resumeCursor;
          }

          sseExpectedTurnIdRef.current = turnId ?? null;
          sseLastSeqRef.current = resumeCursor;
          setProcessingConvId(convId);
          setServerProcessing(!streamId);
          updateAndPersistStreamingState(convId);

          if (streamId) {
            setServerProcessing(false);
            abortRef.current = ac;
            let streamErrorMessage: string | null = null;
            try {
              await api.pollStream(
                streamId,
                (event) => {
                  const result = applyStreamEvent(convId, event);
                  if (result.errorMessage) {
                    streamErrorMessage = result.errorMessage;
                  }
                },
                ac.signal,
                resumeCursor,
              );
              const final = await api.loadMessages(convId, ac.signal);
              processingMsgs.current = final;
              if (activeConvRef.current === convId) {
                setViewedMessages(final);
              }
              const state = getConvStreamingState(convId);
              if (state) {
                clearStreamingStateAfterTurn(
                  state,
                  streamErrorMessage
                    ? { kind: "error", error: streamErrorMessage }
                    : IDLE,
                );
              }
              updateAndPersistStreamingState(convId, { persist: false });
              setProcessingConvId((prev) => (prev === convId ? null : prev));
            } catch (err) {
              if (!isAbortOrUnload(err)) {
                const msg = err instanceof Error ? err.message : String(err);
                const state = getConvStreamingState(convId);
                if (state) {
                  state.assistantError = msg;
                  clearStreamingStateAfterTurn(state, {
                    kind: "error",
                    error: msg,
                  });
                  updateAndPersistStreamingState(convId);
                }
                setProcessingConvId((prev) => (prev === convId ? null : prev));
              }
            } finally {
              if (abortRef.current === ac) {
                abortRef.current = null;
              }
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
  }, [activeConvId, applyStreamEvent, getConvStreamingState, updateAndPersistStreamingState]);

  // While streaming on the active conversation, mirror optimistic history from the ref
  useEffect(() => {
    if (!activeConvId || activeConvId !== processingConvId) return;
    if (processingMsgs.current.length === 0) return;
    setViewedMessages(processingMsgs.current);
    const state = getConvStreamingState(activeConvId);
    if (state?.streamId) {
      setServerProcessing(false);
    }
  }, [activeConvId, getConvStreamingState, processingConvId]);

  // Poll for completion while the server confirms it's actively processing
  useEffect(() => {
    if (!serverProcessing || !activeConvId) return;
    const activeState = getConvStreamingState(activeConvId);
    if (activeState?.streamId) return;

    const convId = activeConvId;
    const ac = new AbortController();

    const interval = setInterval(async () => {
      try {
        const processing = await api.getProcessingStatus(convId, ac.signal);
        if (!processing.processing) {
          clearInterval(interval);
          const msgs = await api.loadMessages(convId, ac.signal);
          if (activeConvRef.current === convId) {
            setViewedMessages(msgs);
          }
          const state = getConvStreamingState(convId);
          if (state) {
            clearStreamingStateAfterTurn(state);
          }
          updateAndPersistStreamingState(convId, { persist: false });
          setProcessingConvId((prev) => (prev === convId ? null : prev));
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
  }, [serverProcessing, activeConvId, getConvStreamingState, updateAndPersistStreamingState]);

  const sendMessage = useCallback(
    async (
      text: string,
      convIdOverride?: string,
      options?: {
        confirmedActionId?: string | null;
        cancelledActionId?: string | null;
        supersededActionId?: string | null;
      },
    ) => {
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
      resetStreamingStateForTurn(state, { turnId: turnUuid });
      sseExpectedTurnIdRef.current = turnUuid;
      sseLastSeqRef.current = 0;
      updateAndPersistStreamingState(convId);

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
            const result = applyStreamEvent(convId, event);
            if (result.errorMessage) {
              streamReportedError = true;
              streamErrorMessage = result.errorMessage;
            }
          },
          controller.signal,
          turnUuid,
          anthropicApiKey,
          modelRef.current,
          options?.confirmedActionId ?? null,
          options?.cancelledActionId ?? null,
          options?.supersededActionId ?? null,
        );

        // Stream finished — load final persisted messages
        const final = await api.loadMessages(convId);
        processingMsgs.current = final;
        if (activeConvRef.current === convId) {
          setViewedMessages(final);
        }
        const state = getConvStreamingState(convId);
        if (state) {
          clearStreamingStateAfterTurn(
            state,
            streamReportedError && streamErrorMessage
              ? { kind: "error", error: streamErrorMessage }
              : IDLE,
          );
        }
        updateAndPersistStreamingState(convId, { persist: false });
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
          clearStreamingStateAfterTurn(
            state,
            state.assistantError
              ? { kind: "error", error: state.assistantError }
              : IDLE,
          );
        }
        updateAndPersistStreamingState(convId);
        setProcessingConvId(null);
      } finally {
        abortRef.current = null;
      }
    },
    [activeConvId, token, processingConvId, anthropicApiKey, applyStreamEvent, getConvStreamingState, updateAndPersistStreamingState]
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
        clearStreamingStateAfterTurn(state);
        // Don't persist to sessionStorage — delete it instead so refresh doesn't auto-restore
      }
      try {
        sessionStorage.removeItem(CTX_USAGE_KEY(activeConvId));
        // Completely remove streaming state so cancel is permanent even after refresh
        sessionStorage.removeItem(STREAMING_STATE_KEY(activeConvId));
      } catch {
        /* ignore */
      }
      // Tell the backend to cancel processing
      try {
        api.cancelProcessing(activeConvId).catch(() => {
          // Ignore if cancel fails — frontend is already stopped
        });
      } catch {
        /* ignore */
      }
    }
    setStreamTokenUsage({ tokens: 0, pct: 0 });
    setServerProcessing(false);
    setProcessingConvId(null);
  }, [activeConvId, getConvStreamingState]);

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
    pendingArtifacts: isViewingProcessing && currentStreamingState ? currentStreamingState.streamArtifacts : [],
    workspaceEvents: isViewingProcessing && currentStreamingState ? currentStreamingState.workspaceEvents : [],
    turnActivity: currentStreamingState?.turnActivity ?? [],
    turnActivityActive: isViewingProcessing,
    processingConvId,
    serverProcessing,
    sendMessage,
    cancel,
    clearAssistantError,
  };
}
