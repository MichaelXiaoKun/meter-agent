import { useCallback, useEffect, useMemo, useState } from "react";
import { getAuthViewFromHash, setHashForAuthView, type AuthGateView } from "./authGate";
import { clearAuth, getStoredAuth, setAuth } from "./authStorage";
import LoginPage from "./components/LoginPage";
import ForgotPasswordPage from "./components/ForgotPasswordPage";
import CheckMailPage from "./components/CheckMailPage";
import EntryChoicePage from "./components/EntryChoicePage";
import SalesChatPage from "./components/SalesChatPage";
import Sidebar from "./components/Sidebar";
import SidebarIconRail from "./components/SidebarIconRail";
import ChatView from "./components/ChatView";
import { ToastContainer, useToast } from "./components/Toast";
import { readStoredModel, writeStoredModel } from "./components/modelPickerStorage";
import { useConversations } from "./hooks/useConversations";
import { useChat } from "./hooks/useChat";
import { useMediaQuery } from "./hooks/useMediaQuery";
import { fetchOrchestratorConfig } from "./api";
import type { OrchestratorModelOption } from "./api";
import {
  cancellationUserMessage,
  confirmationUserMessage,
  type ConfigWorkflow,
} from "./configWorkflowCopy";

function useLocalStorage(key: string, fallback: string) {
  const [value, setValue] = useState(
    () => localStorage.getItem(key) ?? fallback
  );
  const set = useCallback(
    (v: string) => {
      setValue(v);
      if (v) {
        localStorage.setItem(key, v);
      } else {
        localStorage.removeItem(key);
      }
    },
    [key]
  );
  return [value, set] as const;
}

/** Fallback if /api/config fails — matches Haiku Tier-1 ITPM default on the server. */
const DEFAULT_TPM_INPUT_GUIDE = 50_000;
const DEFAULT_MODEL_CONTEXT_WINDOW = 200_000;
/** Fallback safe streamable target for a 50k TPM guide and 2.05× next-call estimate. */
const DEFAULT_MAX_INPUT_TARGET = 24_390;

/** Desktop shelf animation timings (module scope so shelf effect deps stay stable). */
const SHELF_BODY_FADE_MS = 180;
/** Matches ``Sidebar`` New chat shell ``transition-* duration-200``. */
const SHELF_SHELL_MS = 200;
const SHELF_STRIP_MS = Math.max(SHELF_BODY_FADE_MS, SHELF_SHELL_MS);
/** Matches desktop shelf ``transition-[width] duration-200``. */
const SHELF_WIDTH_MS = 200;
const SHELF_SWAP_TAIL_MS = 50;
const SHELF_SWAP_MS =
  Math.max(SHELF_STRIP_MS, SHELF_WIDTH_MS) + SHELF_SWAP_TAIL_MS;

type PreLoginMode = "choice" | "admin" | "sales";

function isSalesRoute(): boolean {
  if (typeof window === "undefined") return false;
  return window.location.pathname === "/sales" || window.location.hash === "#/sales";
}

function initialPreLoginMode(): PreLoginMode {
  if (typeof window === "undefined") return "choice";
  if (isSalesRoute()) return "sales";
  const authView = getAuthViewFromHash();
  if (authView !== "login") return "admin";
  return "choice";
}

export default function App() {
  const toast = useToast();
  const [authView, setAuthView] = useState<AuthGateView>(() => getAuthViewFromHash());
  const [preLoginMode, setPreLoginMode] = useState<PreLoginMode>(() =>
    initialPreLoginMode()
  );
  const [token, setToken] = useState(() => getStoredAuth().token);
  const [user, setUser] = useState(() => getStoredAuth().user);
  const [tpmInputGuideTokens, setTpmInputGuideTokens] =
    useState(DEFAULT_TPM_INPUT_GUIDE);
  const [tpmServerSliding60s, setTpmServerSliding60s] = useState(0);
  const [modelContextWindowTokens, setModelContextWindowTokens] = useState(
    DEFAULT_MODEL_CONTEXT_WINDOW
  );
  const [maxInputTokensTarget, setMaxInputTokensTarget] = useState(
    DEFAULT_MAX_INPUT_TARGET
  );
  const [anthropicApiKey, setAnthropicApiKey] = useLocalStorage(
    "bb_anthropic_key",
    ""
  );
  const [anthropicServerConfigured, setAnthropicServerConfigured] = useState<
    boolean | null
  >(null);
  const [availableModels, setAvailableModels] = useState<
    OrchestratorModelOption[]
  >([]);
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const handleSelectModel = useCallback((modelId: string) => {
    setSelectedModel(modelId);
    writeStoredModel(modelId);
  }, []);

  // TPM guide that tracks the UI's model selection rather than the server default.
  const effectiveTpmGuide = useMemo(() => {
    const id = selectedModel ?? defaultModel;
    if (!id || !availableModels.length) return tpmInputGuideTokens;
    const m = availableModels.find((m) => m.id === id);
    return m && m.tpm_input_guide_tokens > 0 ? m.tpm_input_guide_tokens : tpmInputGuideTokens;
  }, [selectedModel, defaultModel, availableModels, tpmInputGuideTokens]);
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try {
      const v = localStorage.getItem("bb_sidebar_open");
      if (v === "0") return false;
      if (v === "1") return true;
    } catch {
      /* ignore */
    }
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches) {
      return false;
    }
    return true;
  });

  useEffect(() => {
    try {
      localStorage.setItem("bb_sidebar_open", sidebarOpen ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [sidebarOpen]);

  const isNarrow = useMediaQuery("(max-width: 1023px)");

  /**
   * Desktop shelf: decouple **column width** from **which tree is mounted** so
   * collapse can animate ``w-72 → w-14`` while ``Sidebar`` stays mounted and is
   * clipped by ``overflow-hidden`` — no opacity cross-fade (ghosting).
   *
   * Collapse: **parallel** — list/footer fade + New chat shell shrink **and**
   * column ``w-72 → w-14`` start together (``overflow-hidden`` clips content).
   * After ``SHELF_STRIP_MS`` strip body DOM; after ``SHELF_SWAP_MS`` (width
   * transition + buffer) swap ``SidebarIconRail``.
   */
  const [desktopShelfWide, setDesktopShelfWide] = useState(sidebarOpen);
  const [desktopShowFullSidebar, setDesktopShowFullSidebar] =
    useState(sidebarOpen);
  const [collapseShelfBody, setCollapseShelfBody] = useState(false);
  const [collapseShelfFading, setCollapseShelfFading] = useState(false);

  useEffect(() => {
    if (isNarrow) {
      setDesktopShelfWide(sidebarOpen);
      setDesktopShowFullSidebar(sidebarOpen);
      setCollapseShelfBody(false);
      setCollapseShelfFading(false);
      return;
    }
    if (sidebarOpen) {
      setCollapseShelfBody(false);
      setCollapseShelfFading(false);
      setDesktopShowFullSidebar(true);
      const id = requestAnimationFrame(() => {
        requestAnimationFrame(() => setDesktopShelfWide(true));
      });
      return () => cancelAnimationFrame(id);
    }
    const reduceMotion =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) {
      setCollapseShelfFading(false);
      setCollapseShelfBody(false);
      setDesktopShelfWide(false);
      setDesktopShowFullSidebar(false);
      return;
    }
    setCollapseShelfFading(true);
    setDesktopShelfWide(false);
    const tStrip = window.setTimeout(() => {
      setCollapseShelfBody(true);
      setCollapseShelfFading(false);
    }, SHELF_STRIP_MS);
    const tSwap = window.setTimeout(() => {
      setDesktopShowFullSidebar(false);
      setCollapseShelfBody(false);
    }, SHELF_SWAP_MS);
    return () => {
      window.clearTimeout(tStrip);
      window.clearTimeout(tSwap);
    };
  }, [sidebarOpen, isNarrow]);
  const [activeConvId, _setActiveConvId] = useState<string | null>(
    () => localStorage.getItem("bb_active_conv") ?? null
  );
  const setActiveConvId = useCallback((id: string | null) => {
    _setActiveConvId(id);
    if (id) {
      localStorage.setItem("bb_active_conv", id);
    } else {
      localStorage.removeItem("bb_active_conv");
    }
  }, []);

  /**
   * When true, show the empty “New chat” / welcome composer and do not
   * auto-select the first row in the sidebar. Set when:
   *   • this browser has no ``bb_active_conv`` (first open or never picked a
   *     thread — refresh with a stored id still restores that thread);
   *   • the user taps “New chat”; or
   *   • login succeeds (fresh session should land on new chat, not the
   *     oldest conversation).
   * Cleared on sidebar pick, first send (creates a conv), or list reconcile.
   */
  const [userChoseWelcome, setUserChoseWelcome] = useState(() => {
    try {
      return !localStorage.getItem("bb_active_conv");
    } catch {
      return true;
    }
  });

  const isLoggedIn = !!token && !!user;

  const goAuth = useCallback((v: AuthGateView) => {
    setPreLoginMode("admin");
    setAuthView(v);
    setHashForAuthView(v);
  }, []);

  const goSales = useCallback(() => {
    setPreLoginMode("sales");
    if (typeof window !== "undefined" && window.location.hash !== "#/sales") {
      window.history.pushState(null, "", "#/sales");
    }
  }, []);

  const goChoice = useCallback(() => {
    setPreLoginMode("choice");
    setAuthView("login");
    setHashForAuthView("login");
  }, []);

  useEffect(() => {
    const onHash = () => {
      setAuthView(getAuthViewFromHash());
      setPreLoginMode((mode) => {
        if (isSalesRoute()) return "sales";
        return mode === "sales" ? "choice" : mode;
      });
    };
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
    };
  }, []);

  const handleSelectConversation = useCallback(
    (id: string) => {
      setUserChoseWelcome(false);
      setActiveConvId(id);
      if (isNarrow) setSidebarOpen(false);
    },
    [isNarrow, setActiveConvId]
  );

  useEffect(() => {
    const load = () => {
      fetchOrchestratorConfig()
        .then((c) => {
          if (typeof c.tpm_input_guide_tokens === "number" && c.tpm_input_guide_tokens > 0) {
            setTpmInputGuideTokens(c.tpm_input_guide_tokens);
          }
          if (typeof c.model_context_window === "number" && c.model_context_window > 0) {
            setModelContextWindowTokens(c.model_context_window);
          }
          if (typeof c.max_input_tokens_target === "number" && c.max_input_tokens_target > 0) {
            setMaxInputTokensTarget(c.max_input_tokens_target);
          }
          if (typeof c.tpm_sliding_input_tokens_60s === "number") {
            setTpmServerSliding60s(Math.max(0, c.tpm_sliding_input_tokens_60s));
          }
          if (typeof c.anthropic_server_configured === "boolean") {
            setAnthropicServerConfigured(c.anthropic_server_configured);
          }
          if (Array.isArray(c.available_models)) {
            setAvailableModels(c.available_models);
            const dflt = typeof c.default_model === "string" ? c.default_model : null;
            setDefaultModel(dflt);
            // Only seed on first load: keep user's stored pick across polls
            // so a /api/config refresh doesn't clobber a freshly-picked model
            // with the server default.
            setSelectedModel((prev) =>
              prev ?? readStoredModel(c.available_models, dflt ?? undefined),
            );
          }
        })
        .catch(() => { });
    };
    load();
    const id = window.setInterval(load, 2000);
    return () => window.clearInterval(id);
  }, []);

  const { conversations, listLoaded, refresh, create, remove, removeMany, rename } =
    useConversations(user);

  const activeConversationTitle = useMemo(() => {
    if (!activeConvId) return "Conversation";
    return conversations.find((c) => c.id === activeConvId)?.title?.trim() || "Conversation";
  }, [conversations, activeConvId]);

  const {
    messages,
    status,
    streamingLead,
    streamingTail,
    tokenUsage,
    historyLoading,
    pendingPlots,
    pendingArtifacts,
    workspaceEvents,
    turnActivity,
    turnActivityActive,
    processingConvId,
    serverProcessing,
    sendMessage,
    cancel,
    clearAssistantError,
  } = useChat(activeConvId, token, anthropicApiKey, selectedModel);

  // Keep the selected chat in sync with the server list. If the user is on
  // the welcome screen after “New chat” (``userChoseWelcome``), do not
  // auto-pick a conversation. Otherwise restore a missing id to the first row.
  useEffect(() => {
    if (!listLoaded) return;
    if (conversations.length === 0) {
      if (activeConvId !== null) setActiveConvId(null);
      setUserChoseWelcome(false);
      return;
    }
    if (activeConvId == null) {
      if (userChoseWelcome) return;
      setActiveConvId(conversations[0]!.id);
      return;
    }
    if (!conversations.some((c) => c.id === activeConvId)) {
      setUserChoseWelcome(false);
      setActiveConvId(conversations[0]!.id);
    }
  }, [conversations, activeConvId, setActiveConvId, listLoaded, userChoseWelcome]);

  function handleLogin(
    accessToken: string,
    username: string,
    options?: { persist?: boolean }
  ) {
    const persist = options?.persist !== false;
    setAuth(accessToken, username, { persist });
    setToken(accessToken);
    setUser(username);
    setUserChoseWelcome(true);
    setActiveConvId(null);
    setPreLoginMode("admin");
    setAuthView("login");
    setHashForAuthView("login");
  }

  function handleLogout() {
    cancel();
    clearAuth();
    setToken("");
    setUser("");
    setUserChoseWelcome(false);
    setActiveConvId(null);
    localStorage.removeItem("bb_active_conv");
    setAnthropicApiKey("");
    goChoice();
  }

  function handleNewConversation() {
    if (!user) return;
    cancel();
    setUserChoseWelcome(true);
    setActiveConvId(null);
    if (isNarrow) setSidebarOpen(false);
  }

  async function handleDeleteConversation(id: string) {
    if (id === activeConvId || id === processingConvId) {
      cancel();
    }
    const wasActive = id === activeConvId;
    const list = await remove(id);
    if (!wasActive) return;
    setUserChoseWelcome(false);
    setActiveConvId(list && list.length > 0 ? list[0]!.id : null);
  }

  async function handleDeleteConversations(ids: string[]) {
    if (ids.length === 0) return;
    const idSet = new Set(ids);
    const hadActive = activeConvId != null && idSet.has(activeConvId);
    if (hadActive || (processingConvId && idSet.has(processingConvId))) {
      cancel();
    }
    const list = await removeMany(ids);
    if (hadActive) {
      setUserChoseWelcome(false);
      setActiveConvId(list && list.length > 0 ? list[0]!.id : null);
    }
  }

  async function handleSend(
    text: string,
    options?: {
      confirmedActionId?: string | null;
      cancelledActionId?: string | null;
      supersededActionId?: string | null;
    },
  ) {
    let convId = activeConvId;
    if (!convId && user) {
      convId = await create();
      if (convId) {
        setUserChoseWelcome(false);
        setActiveConvId(convId);
      }
    }
    if (!convId) return;
    sendMessage(text, convId, options).then(() => refresh());
  }

  function handleConfirmConfig(workflow: ConfigWorkflow) {
    const actionId = workflow.action_id;
    if (!actionId) return;
    const message = confirmationUserMessage(workflow);
    void handleSend(message, { confirmedActionId: actionId });
  }

  function handleCancelConfig(workflow: ConfigWorkflow) {
    const actionId = workflow.action_id;
    if (!actionId) return;
    void handleSend(cancellationUserMessage(), {
      cancelledActionId: actionId,
    });
  }

  if (!isLoggedIn) {
    if (preLoginMode === "sales") {
      return <SalesChatPage onBackToEntry={goChoice} />;
    }
    if (preLoginMode === "choice") {
      return (
        <EntryChoicePage
          onChooseAdmin={() => {
            setPreLoginMode("admin");
            goAuth("login");
          }}
          onChooseSales={goSales}
        />
      );
    }
    if (authView === "forgot") {
      return (
        <ForgotPasswordPage
          onBackToLogin={() => goAuth("login")}
          onSuccess={() => goAuth("check-mail")}
        />
      );
    }
    if (authView === "check-mail") {
      return <CheckMailPage onBackToLogin={() => goAuth("login")} />;
    }
    return (
      <LoginPage
        onLogin={handleLogin}
        onForgotPassword={() => goAuth("forgot")}
        onBackToEntry={() => {
          goChoice();
        }}
      />
    );
  }

  const sidebarProps = {
    conversations,
    activeId: activeConvId,
    processingId: processingConvId,
    user,
    onSelectConversation: handleSelectConversation,
    onNewConversation: handleNewConversation,
    onDeleteConversation: handleDeleteConversation,
    onDeleteConversations: handleDeleteConversations,
    onRenameConversation: rename,
    onLogout: handleLogout,
    anthropicApiKey,
    onAnthropicApiKeyChange: setAnthropicApiKey,
    anthropicServerConfigured,
    onCollapse: () => setSidebarOpen(false),
    collapseShelfBody,
    collapseShelfFading,
  };

  return (
    <div className="relative flex h-[100dvh] max-h-[100dvh] min-h-0 overflow-hidden overflow-x-hidden bg-brand-50 text-brand-900">
      {/*
        Mobile drawer — kept in the DOM while ``isNarrow`` is true so the
        slide-in/out is a real CSS transition instead of a mount pop.
        When closed we translate it off-screen and make the backdrop +
        drawer inert (``pointer-events-none``, ``aria-hidden``, ``tabIndex -1``)
        so nothing invisible can be tapped or focused behind the scenes.
      */}
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
            : `h-[100dvh] max-h-[100dvh] border-r border-brand-border bg-gradient-to-b from-white/95 to-brand-100 dark:bg-gradient-to-b dark:from-brand-50 dark:to-brand-50 ${desktopShelfWide ? "w-72" : "w-14"}`
          }`}
      >
        {!isNarrow && desktopShowFullSidebar ? (
          <Sidebar {...sidebarProps} />
        ) : !isNarrow && !desktopShowFullSidebar ? (
          <div className="flex h-full min-h-0 min-w-0 w-full flex-1 flex-col bg-gradient-to-b from-white/95 to-brand-100 dark:bg-gradient-to-b dark:from-brand-50 dark:to-brand-50">
            <SidebarIconRail
              onExpand={() => setSidebarOpen(true)}
              onNewConversation={handleNewConversation}
              user={user}
              onLogout={handleLogout}
            />
          </div>
        ) : null}
      </div>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <ChatView
          conversationId={activeConvId}
          messages={messages}
          status={status}
          streamingLead={streamingLead}
          streamingTail={streamingTail}
          pendingPlots={pendingPlots}
          pendingArtifacts={pendingArtifacts}
          workspaceEvents={workspaceEvents}
          tokenUsage={tokenUsage}
          historyLoading={historyLoading}
          tpmInputGuideTokens={effectiveTpmGuide}
          tpmServerSliding60s={tpmServerSliding60s}
          modelContextWindowTokens={modelContextWindowTokens}
          maxInputTokensTarget={maxInputTokensTarget}
          turnActivity={turnActivity}
          turnActivityActive={turnActivityActive}
          serverProcessing={serverProcessing}
          onSend={handleSend}
          onConfirmConfig={handleConfirmConfig}
          onCancelConfig={handleCancelConfig}
          onCancel={cancel}
          onDismissAssistantError={clearAssistantError}
          disabled={false}
          availableModels={availableModels}
          selectedModel={selectedModel ?? defaultModel}
          onSelectModel={handleSelectModel}
          accessToken={token}
          anthropicApiKey={anthropicApiKey}
          onToast={(a) => {
            if (a.kind === "success") {
              toast.success(a.title, a.message);
            } else {
              toast.error(a.title, a.message);
            }
          }}
          share={
            user && token
              ? {
                userId: user,
                accessToken: token,
                conversationTitle: activeConversationTitle,
                onToast: (a) => {
                  if (a.kind === "success") {
                    toast.success(a.title, a.message);
                  } else {
                    toast.error(a.title, a.message);
                  }
                },
              }
              : undefined
          }
          narrowNav={
            isNarrow
              ? {
                onOpenSidebar: () => setSidebarOpen(true),
              }
              : undefined
          }
        />
      </main>
      <ToastContainer
        toasts={toast.toasts}
        onClose={toast.dismiss}
      />
    </div>
  );
}
