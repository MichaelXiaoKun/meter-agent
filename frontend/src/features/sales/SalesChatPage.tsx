import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelSalesProcessing,
  createSalesShare,
  getSalesProcessingStatus,
  loadSalesConversation,
  pollSalesStream,
  revokeSalesShare,
  streamSalesChat,
  type SalesSSEEvent,
} from "../../api/client";
import {
  applyStreamEventToChatState,
  clearChatStreamStateAfterTurn,
  createChatStreamState,
  IDLE,
  resetChatStreamStateForTurn,
  type ChatStreamState,
} from "../../core/chatStreamReducer";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useSalesConversations } from "../../hooks/useSalesConversations";
import type { Message, SSEEvent } from "../../core/types";
import ChatView from "../chat/components/ChatView";
import Sidebar from "../conversations/components/Sidebar";
import SidebarIconRail from "../conversations/components/SidebarIconRail";
import { ToastContainer } from "../feedback/components/Toast";
import { useToast } from "../feedback/useToast";
import type { QuickAction } from "../chat/components/WelcomeCard";

interface SalesChatPageProps {
  onBackToEntry?: () => void;
}

const SALES_ACTIVE_CONV_KEY = "bb_sales_active_conv";
const LEGACY_SALES_CONV_KEY = "bb_sales_conv";
const SALES_STREAMING_STATE_KEY = (conversationId: string) =>
  `bb_sales_streaming_state_${conversationId}`;

const SALES_ACTIONS: QuickAction[] = [
  {
    id: "pipe-impact",
    label: "Pipe impact",
    message: () => "Will Bluebot damage my pipe or affect water pressure?",
  },
  {
    id: "unknown-size",
    label: "Unknown size",
    message: () => "I do not know my pipe size. What should I check?",
  },
  {
    id: "irrigation",
    label: "Irrigation",
    message: () => "Can Bluebot work for irrigation monitoring?",
  },
  {
    id: "quote-info",
    label: "Quote info",
    message: () => "What information do you need to recommend the right meter?",
  },
];

function turnId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `sales-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function readInitialActiveId(): string | null {
  try {
    return (
      localStorage.getItem(SALES_ACTIVE_CONV_KEY) ||
      localStorage.getItem(LEGACY_SALES_CONV_KEY)
    );
  } catch {
    return null;
  }
}

function isAbortOrUnload(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof TypeError && /load failed/i.test(err.message)) return true;
  return false;
}

function serializeSalesStreamState(state: ChatStreamState): string {
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

function deserializeSalesStreamState(json: string): ChatStreamState {
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
    return createChatStreamState();
  }
}

function readStoredSalesStreamState(conversationId: string | null): ChatStreamState | null {
  if (!conversationId || typeof sessionStorage === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(SALES_STREAMING_STATE_KEY(conversationId));
    return raw ? deserializeSalesStreamState(raw) : null;
  } catch {
    return null;
  }
}

export default function SalesChatPage({ onBackToEntry }: SalesChatPageProps) {
  const toast = useToast();
  const {
    conversations,
    listLoaded,
    refresh,
    create,
    remove,
    removeMany,
    rename,
  } = useSalesConversations();
  const [activeConvId, _setActiveConvId] = useState<string | null>(
    readInitialActiveId
  );
  const [messages, setMessages] = useState<Message[]>([]);
  const streamStateMapRef = useRef<Map<string, ChatStreamState>>(new Map());
  const [streamState, setStreamState] = useState(() => createChatStreamState());
  const [processingConvIds, setProcessingConvIds] = useState<string[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches) {
      return false;
    }
    return true;
  });
  const streamAbortMapRef = useRef<Map<string, AbortController>>(new Map());
  const historyAbortRef = useRef<AbortController | null>(null);
  const activeConvRef = useRef(activeConvId);
  const sendInFlightRef = useRef(false);
  const processingConvIdsRef = useRef<Set<string>>(new Set());
  const prevLoadedConvIdRef = useRef<string | null>(null);
  const turnRefsMapRef = useRef<
    Map<string, { expectedTurnId: { current: string | null }; lastSeq: { current: number } }>
  >(new Map());
  const isNarrow = useMediaQuery("(max-width: 1023px)");

  const setProcessingSet = useCallback((next: Set<string>) => {
    processingConvIdsRef.current = next;
    setProcessingConvIds([...next]);
  }, []);

  const addProcessingConvId = useCallback((id: string) => {
    const next = new Set(processingConvIdsRef.current);
    next.add(id);
    setProcessingSet(next);
  }, [setProcessingSet]);

  const removeProcessingConvId = useCallback((id: string) => {
    const next = new Set(processingConvIdsRef.current);
    next.delete(id);
    setProcessingSet(next);
  }, [setProcessingSet]);

  const getTurnRefs = useCallback((convId: string) => {
    const map = turnRefsMapRef.current;
    if (!map.has(convId)) {
      map.set(convId, {
        expectedTurnId: { current: null },
        lastSeq: { current: 0 },
      });
    }
    return map.get(convId)!;
  }, []);

  const resetTurnRefs = useCallback((convId: string) => {
    turnRefsMapRef.current.delete(convId);
  }, []);

  const getStreamState = useCallback((convId: string | null) => {
    if (!convId) return null;
    const map = streamStateMapRef.current;
    if (!map.has(convId)) {
      map.set(convId, readStoredSalesStreamState(convId) ?? createChatStreamState());
    }
    return map.get(convId) ?? null;
  }, []);

  const persistStreamState = useCallback((convId: string, state: ChatStreamState) => {
    try {
      sessionStorage.setItem(
        SALES_STREAMING_STATE_KEY(convId),
        serializeSalesStreamState(state),
      );
    } catch {
      /* ignore quota / private mode */
    }
  }, []);

  const clearStoredStreamState = useCallback((convId: string) => {
    try {
      sessionStorage.removeItem(SALES_STREAMING_STATE_KEY(convId));
    } catch {
      /* ignore */
    }
  }, []);

  const publishStreamState = useCallback((convId = activeConvRef.current) => {
    if (!convId || convId !== activeConvRef.current) return;
    const state = getStreamState(convId);
    setStreamState(state ? { ...state } : createChatStreamState());
  }, [getStreamState]);

  useEffect(() => {
    activeConvRef.current = activeConvId;
    const state = getStreamState(activeConvId);
    setStreamState(state ? { ...state } : createChatStreamState());
  }, [activeConvId, getStreamState]);

  const setActiveConvId = useCallback((id: string | null) => {
    activeConvRef.current = id;
    _setActiveConvId(id);
    try {
      if (id) {
        localStorage.setItem(SALES_ACTIVE_CONV_KEY, id);
        localStorage.setItem(LEGACY_SALES_CONV_KEY, id);
      } else {
        localStorage.removeItem(SALES_ACTIVE_CONV_KEY);
        localStorage.removeItem(LEGACY_SALES_CONV_KEY);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const isViewingProcessing =
    activeConvId != null && processingConvIds.includes(activeConvId);
  const status = isViewingProcessing
    ? streamState.streamStatus ?? IDLE
    : streamState.assistantError
      ? { kind: "error" as const, error: streamState.assistantError }
      : IDLE;
  const isProcessing = status.kind !== "idle" && status.kind !== "error";
  const processingId = processingConvIds[0] ?? null;
  const composerDisabledMessage =
    (historyLoading || !listLoaded) && !isProcessing
      ? "Loading sales conversations..."
      : undefined;

  useEffect(() => {
    if (!listLoaded) return;
    if (!activeConvId) {
      if (conversations.length > 0) {
        setActiveConvId(conversations[0]!.id);
      }
      return;
    }
    if (!conversations.some((c) => c.id === activeConvId)) {
      setActiveConvId(conversations[0]?.id ?? null);
    }
  }, [activeConvId, conversations, listLoaded, setActiveConvId]);

  const applyEvent = useCallback((convId: string, event: SalesSSEEvent) => {
    const state = getStreamState(convId);
    if (!state) return;
    const refs = getTurnRefs(convId);
    applyStreamEventToChatState(
      state,
      event as SSEEvent,
      refs,
    );
    persistStreamState(convId, state);
    publishStreamState(convId);
  }, [getStreamState, getTurnRefs, persistStreamState, publishStreamState]);

  useEffect(() => {
    historyAbortRef.current?.abort();
    if (!activeConvId) {
      prevLoadedConvIdRef.current = null;
      setMessages([]);
      setHistoryLoading(false);
      return;
    }
    const switched =
      prevLoadedConvIdRef.current !== null &&
      prevLoadedConvIdRef.current !== activeConvId;
    prevLoadedConvIdRef.current = activeConvId;
    if (switched) setMessages([]);

    const ac = new AbortController();
    const convId = activeConvId;
    historyAbortRef.current = ac;
    setHistoryLoading(true);
    loadSalesConversation(convId, ac.signal)
      .then(async (loaded) => {
        if (activeConvRef.current !== convId) return;
        setMessages(loaded.messages || []);
        const processing = await getSalesProcessingStatus(convId, ac.signal);
        if (!processing.processing || !processing.stream_id) {
          if (processingConvIdsRef.current.has(convId)) {
            removeProcessingConvId(convId);
            resetTurnRefs(convId);
          }
          return;
        }
        if (
          processingConvIdsRef.current.has(convId) &&
          streamAbortMapRef.current.has(convId)
        ) {
          return;
        }

        const state = getStreamState(convId);
        if (!state) return;
        const streamId = processing.stream_id;
        const turnId = processing.turn_id ?? state.turnId;
        const sameStoredRun =
          state.streamId === streamId ||
          (turnId != null && state.turnId === turnId);
        const resumeCursor = sameStoredRun ? Math.max(0, state.cursor) : 0;
        if (
          !sameStoredRun ||
          (state.turnActivity.length === 0 && state.streamStatus.kind === "idle")
        ) {
          resetChatStreamStateForTurn(state, {
            status: { kind: "connecting" },
            streamId,
            turnId: turnId ?? null,
            cursor: resumeCursor,
            title: "Reconnecting to current turn",
          });
        } else {
          state.streamId = streamId;
          state.turnId = turnId ?? null;
          state.cursor = resumeCursor;
        }
        const refs = getTurnRefs(convId);
        refs.expectedTurnId.current = turnId ?? null;
        refs.lastSeq.current = resumeCursor;
        addProcessingConvId(convId);
        persistStreamState(convId, state);
        publishStreamState(convId);

        const streamAc = new AbortController();
        streamAbortMapRef.current.set(convId, streamAc);
        let streamErrorMessage: string | null = null;
        try {
          await pollSalesStream(
            streamId,
            (event) => {
              applyEvent(convId, event);
              if (event.type === "error") {
                streamErrorMessage = event.error ?? event.message ?? "Sales chat failed";
              }
            },
            streamAc.signal,
            resumeCursor,
          );
          const final = await loadSalesConversation(convId, streamAc.signal);
          if (activeConvRef.current === convId) {
            setMessages(final.messages || []);
          }
          clearChatStreamStateAfterTurn(
            state,
            streamErrorMessage
              ? { kind: "error", error: streamErrorMessage }
              : IDLE,
          );
          clearStoredStreamState(convId);
          publishStreamState(convId);
          removeProcessingConvId(convId);
          resetTurnRefs(convId);
          await refresh();
        } catch (e) {
          if (!isAbortOrUnload(e)) {
            const error = e instanceof Error ? e.message : String(e);
            state.assistantError = error;
            state.streamStatus = { kind: "error", error };
            persistStreamState(convId, state);
            publishStreamState(convId);
            removeProcessingConvId(convId);
            resetTurnRefs(convId);
          }
        } finally {
          if (streamAbortMapRef.current.get(convId) === streamAc) {
            streamAbortMapRef.current.delete(convId);
          }
        }
      })
      .catch((e) => {
        if (ac.signal.aborted) return;
        const state = getStreamState(convId);
        if (!state) return;
        state.streamStatus = {
          kind: "error",
          error: e instanceof Error ? e.message : String(e),
        };
        persistStreamState(convId, state);
        publishStreamState(convId);
      })
      .finally(() => {
        if (!ac.signal.aborted && activeConvRef.current === convId) {
          setHistoryLoading(false);
        }
        if (historyAbortRef.current === ac) {
          historyAbortRef.current = null;
        }
      });
    return () => ac.abort();
  }, [
    activeConvId,
    applyEvent,
    clearStoredStreamState,
    getStreamState,
    addProcessingConvId,
    persistStreamState,
    publishStreamState,
    refresh,
    removeProcessingConvId,
    resetTurnRefs,
    getTurnRefs,
  ]);

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || status.kind !== "idle") return;
    if (sendInFlightRef.current) return;
    sendInFlightRef.current = true;
    let convId = activeConvId;
    let state: ChatStreamState | null = null;
    let ac: AbortController | null = null;
    try {
      if (!convId) {
        convId = await create();
        setActiveConvId(convId);
      }
      if (
        processingConvIdsRef.current.has(convId) ||
        streamAbortMapRef.current.has(convId)
      ) {
        return;
      }
      const sendConvId = convId;
      state = getStreamState(sendConvId);
      if (!state) return;
      addProcessingConvId(sendConvId);
      const clientTurnId = turnId();
      const refs = getTurnRefs(sendConvId);
      refs.expectedTurnId.current = clientTurnId;
      refs.lastSeq.current = 0;
      resetChatStreamStateForTurn(state, {
        status: { kind: "connecting" },
        turnId: clientTurnId,
        title: "Sending your message",
      });
      persistStreamState(sendConvId, state);
      publishStreamState(sendConvId);
      setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
      ac = new AbortController();
      streamAbortMapRef.current.set(sendConvId, ac);
      await streamSalesChat(
        sendConvId,
        trimmed,
        (event) => applyEvent(sendConvId, event),
        ac.signal,
        clientTurnId,
      );
      const loaded = await loadSalesConversation(sendConvId);
      if (activeConvRef.current === sendConvId) {
        setMessages(loaded.messages || []);
      }
      clearChatStreamStateAfterTurn(state);
      clearStoredStreamState(sendConvId);
      publishStreamState(sendConvId);
      removeProcessingConvId(sendConvId);
      resetTurnRefs(sendConvId);
      await refresh();
    } catch (e) {
      if (state && ac && !ac.signal.aborted && convId) {
        const error = e instanceof Error ? e.message : String(e);
        state.assistantError = error;
        state.streamStatus = { kind: "error", error };
        persistStreamState(convId, state);
        publishStreamState(convId);
      }
      if (convId) {
        removeProcessingConvId(convId);
        resetTurnRefs(convId);
      }
    } finally {
      sendInFlightRef.current = false;
      if (convId && ac && streamAbortMapRef.current.get(convId) === ac) {
        streamAbortMapRef.current.delete(convId);
      }
    }
  }, [
    activeConvId,
    addProcessingConvId,
    applyEvent,
    clearStoredStreamState,
    create,
    getTurnRefs,
    getStreamState,
    persistStreamState,
    publishStreamState,
    refresh,
    removeProcessingConvId,
    resetTurnRefs,
    setActiveConvId,
    status.kind,
  ]);

  const handleNewConversation = useCallback(async () => {
    if (historyLoading || !listLoaded) return;
    const activeMeta = activeConvId
      ? conversations.find((c) => c.id === activeConvId)
      : null;
    const activeIsEmpty =
      !activeConvId ||
      (messages.length === 0 &&
        streamState.streamLead.length === 0 &&
        streamState.streamTail.length === 0 &&
        (activeMeta?.message_count ?? 0) === 0);
    if (activeIsEmpty) return;

    setMessages([]);
    try {
      const id = await create();
      setActiveConvId(id);
      if (isNarrow) setSidebarOpen(false);
    } catch (e) {
      toast.error(
        "Could not create chat",
        e instanceof Error ? e.message : "The sales conversation could not be created.",
      );
    }
  }, [
    activeConvId,
    conversations,
    create,
    historyLoading,
    isNarrow,
    listLoaded,
    messages.length,
    setActiveConvId,
    streamState.streamLead.length,
    streamState.streamTail.length,
    toast,
  ]);

  const handleSelectConversation = useCallback((id: string) => {
    setActiveConvId(id);
    if (isNarrow) setSidebarOpen(false);
  }, [isNarrow, setActiveConvId]);

  const handleDeleteConversation = useCallback(
    async (id: string) => {
      if (processingConvIdsRef.current.has(id)) {
        streamAbortMapRef.current.get(id)?.abort();
        void cancelSalesProcessing(id).catch(() => {
          /* deleting locally anyway */
        });
        removeProcessingConvId(id);
        resetTurnRefs(id);
      }
      const wasActive = id === activeConvId;
      streamStateMapRef.current.delete(id);
      clearStoredStreamState(id);
      const list = await remove(id);
      if (wasActive) setActiveConvId(list?.[0]?.id ?? null);
    },
    [
      activeConvId,
      clearStoredStreamState,
      remove,
      setActiveConvId,
      removeProcessingConvId,
      resetTurnRefs,
    ],
  );

  const handleDeleteConversations = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0) return;
      const idSet = new Set(ids);
      const hadActive = activeConvId != null && idSet.has(activeConvId);
      ids.forEach((id) => {
        if (processingConvIdsRef.current.has(id)) {
          streamAbortMapRef.current.get(id)?.abort();
          void cancelSalesProcessing(id).catch(() => {
            /* deleting locally anyway */
          });
          removeProcessingConvId(id);
          resetTurnRefs(id);
        }
        streamStateMapRef.current.delete(id);
        clearStoredStreamState(id);
      });
      const list = await removeMany(ids);
      if (hadActive) setActiveConvId(list?.[0]?.id ?? null);
    },
    [
      activeConvId,
      clearStoredStreamState,
      removeMany,
      setActiveConvId,
      removeProcessingConvId,
      resetTurnRefs,
    ],
  );

  const goBack = useCallback(() => {
    streamAbortMapRef.current.forEach((controller) => controller.abort());
    streamAbortMapRef.current.clear();
    historyAbortRef.current?.abort();
    if (onBackToEntry) {
      onBackToEntry();
    } else {
      window.location.href = "/";
    }
  }, [onBackToEntry]);

  const cancel = useCallback(() => {
    if (!activeConvId) return;
    const shouldCancelServer =
      processingConvIdsRef.current.has(activeConvId) ||
      streamAbortMapRef.current.has(activeConvId);
    if (shouldCancelServer) {
      streamAbortMapRef.current.get(activeConvId)?.abort();
    }
    streamAbortMapRef.current.delete(activeConvId);
    resetTurnRefs(activeConvId);
    const state = getStreamState(activeConvId);
    if (state) {
      state.assistantError = null;
      clearChatStreamStateAfterTurn(state);
      clearStoredStreamState(activeConvId);
      publishStreamState(activeConvId);
    }
    removeProcessingConvId(activeConvId);
    if (shouldCancelServer) {
      void cancelSalesProcessing(activeConvId).catch(() => {
        /* frontend already stopped */
      });
    }
  }, [
    activeConvId,
    clearStoredStreamState,
    getStreamState,
    publishStreamState,
    removeProcessingConvId,
    resetTurnRefs,
  ]);

  const dismissAssistantError = useCallback(() => {
    if (!activeConvId) return;
    const state = getStreamState(activeConvId);
    if (!state) return;
    state.assistantError = null;
    state.streamStatus = IDLE;
    clearStoredStreamState(activeConvId);
    publishStreamState(activeConvId);
  }, [activeConvId, clearStoredStreamState, getStreamState, publishStreamState]);

  const sidebarProps = {
    conversations,
    activeId: activeConvId,
    processingId,
    processingIds: processingConvIds,
    user: "Sales guest",
    onSelectConversation: handleSelectConversation,
    onNewConversation: handleNewConversation,
    onDeleteConversation: handleDeleteConversation,
    onDeleteConversations: handleDeleteConversations,
    onRenameConversation: rename,
    onLogout: goBack,
    anthropicApiKey: "",
    onAnthropicApiKeyChange: () => undefined,
    anthropicServerConfigured: true,
    showApiKeyControl: false,
    accountLabel: "Mode:",
    logoutLabel: "Back to options",
    onCollapse: () => setSidebarOpen(false),
  };
  const activeConversationTitle =
    conversations.find((conversation) => conversation.id === activeConvId)?.title ||
    "Sales conversation";

  return (
    <div className="relative flex h-[100dvh] max-h-[100dvh] min-h-0 overflow-hidden overflow-x-hidden bg-brand-50 text-brand-900">
      {isNarrow && (
        <>
          <button
            type="button"
            className={`fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-[1px] transition-opacity duration-300 ease-out dark:bg-black/60 lg:hidden ${sidebarOpen
              ? "opacity-100"
              : "pointer-events-none opacity-0"
              }`}
            aria-label="Close sidebar"
            aria-hidden={!sidebarOpen}
            tabIndex={sidebarOpen ? 0 : -1}
            onClick={() => setSidebarOpen(false)}
          />
          <div
            className={`fixed inset-y-0 left-0 z-50 flex h-[100dvh] max-h-[100dvh] min-w-0 overflow-hidden border-r border-brand-border bg-gradient-to-b from-white/95 to-brand-100 shadow-2xl transition-transform duration-300 ease-out will-change-transform dark:bg-gradient-to-b dark:from-brand-50 dark:to-brand-50 lg:hidden [width:min(20rem,calc(100dvw_-_env(safe-area-inset-left,0px)_-_env(safe-area-inset-right,0px)))] max-w-[min(20rem,calc(100dvw_-_env(safe-area-inset-left,0px)_-_env(safe-area-inset-right,0px)))] ${sidebarOpen
              ? "translate-x-0"
              : "pointer-events-none -translate-x-full"
              }`}
            aria-hidden={!sidebarOpen}
          >
            <div className="h-full min-h-0 min-w-0 flex-1 overflow-hidden [&>aside]:max-w-full [&>aside]:min-w-0 [&>aside]:w-full">
              <Sidebar {...sidebarProps} />
            </div>
          </div>
        </>
      )}

      <div
        className={`relative z-[45] flex min-h-0 shrink-0 flex-col overflow-hidden transition-[width] duration-200 ease-[cubic-bezier(0.25,0.46,0.45,0.94)] motion-reduce:transition-none motion-reduce:duration-0 ${isNarrow
          ? "w-0 min-w-0 border-r-0"
          : `h-[100dvh] max-h-[100dvh] border-r border-brand-border bg-gradient-to-b from-white/95 to-brand-100 dark:bg-gradient-to-b dark:from-brand-50 dark:to-brand-50 ${sidebarOpen ? "w-72" : "w-14"}`
          }`}
      >
        {!isNarrow && sidebarOpen ? (
          <Sidebar {...sidebarProps} />
        ) : !isNarrow && !sidebarOpen ? (
          <div className="flex h-full min-h-0 min-w-0 w-full flex-1 flex-col bg-gradient-to-b from-white/95 to-brand-100 dark:bg-gradient-to-b dark:from-brand-50 dark:to-brand-50">
            <SidebarIconRail
              onExpand={() => setSidebarOpen(true)}
              onNewConversation={handleNewConversation}
              user="Sales"
              onLogout={goBack}
            />
          </div>
        ) : null}
      </div>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <ChatView
          conversationId={activeConvId}
          messages={messages}
          status={status}
          streamingLead={isViewingProcessing ? streamState.streamLead : ""}
          streamingTail={isViewingProcessing ? streamState.streamTail : ""}
          pendingPlots={isViewingProcessing ? streamState.streamPlots : []}
          pendingArtifacts={isViewingProcessing ? streamState.streamArtifacts : []}
          tokenUsage={{ tokens: 0, pct: 0 }}
          historyLoading={historyLoading || !listLoaded}
          tpmInputGuideTokens={50_000}
          tpmServerSliding60s={0}
          modelContextWindowTokens={200_000}
          maxInputTokensTarget={24_390}
          turnActivity={streamState.turnActivity}
          turnActivityActive={isViewingProcessing}
          workspaceEvents={isViewingProcessing ? streamState.workspaceEvents : []}
          serverProcessing={false}
          onSend={(text) => void send(text)}
          onConfirmConfig={() => undefined}
          onCancelConfig={() => undefined}
          onCancel={cancel}
          onDismissAssistantError={dismissAssistantError}
          disabled={(historyLoading || !listLoaded) && !isProcessing}
          disabledMessage={composerDisabledMessage}
          narrowNav={
            isNarrow
              ? {
                onOpenSidebar: () => setSidebarOpen(true),
              }
              : undefined
          }
          onToast={(a) => {
            if (a.kind === "success") toast.success(a.title, a.message);
            else toast.error(a.title, a.message);
          }}
          share={{
            conversationTitle: activeConversationTitle,
            onToast: (a) => {
              if (a.kind === "success") toast.success(a.title, a.message);
              else toast.error(a.title, a.message);
            },
            createShareLink: createSalesShare,
            revokeShareLink: revokeSalesShare,
          }}
          copy={{
            title: "FlowIQ Sales",
            titleClassName: "text-brand-700 dark:text-brand-700",
            subtitle: "by bluebot · Product fit, pipe impact, and buyer qualification.",
            welcomeTitle: "What are you trying to monitor?",
            welcomePlaceholder: "Ask about product fit, installation, or pipe impact...",
            composerPlaceholder: "Ask about product fit, installation, or pipe impact...",
            welcomeActions: SALES_ACTIONS,
            welcomeHint: "Start with a question, or tell me your pipe material, size, liquid, and application.",
            requireWelcomeSerial: false,
          }}
          showWorkspacePanel={false}
        />
      </main>
      <ToastContainer toasts={toast.toasts} onClose={toast.dismiss} />
    </div>
  );
}
