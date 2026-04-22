import { useCallback, useEffect, useState } from "react";
import LoginPage from "./components/LoginPage";
import Sidebar from "./components/Sidebar";
import SidebarIconRail from "./components/SidebarIconRail";
import ChatView from "./components/ChatView";
import { readStoredModel, writeStoredModel } from "./components/modelPickerStorage";
import { useConversations } from "./hooks/useConversations";
import { useChat } from "./hooks/useChat";
import { useMediaQuery } from "./hooks/useMediaQuery";
import { fetchOrchestratorConfig } from "./api";
import type { OrchestratorModelOption } from "./api";

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
/** Default 0.5 × TPM guide when config omits max_input_tokens_target. */
const DEFAULT_MAX_INPUT_TARGET = 25_000;

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

export default function App() {
  const [token, setToken] = useLocalStorage("bb_token", "");
  const [user, setUser] = useLocalStorage("bb_user", "");
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

  const isLoggedIn = !!token && !!user;

  const handleSelectConversation = useCallback(
    (id: string) => {
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

  const { conversations, refresh, create, remove, rename } = useConversations(user);
  const {
    messages,
    status,
    streamingText,
    tokenUsage,
    historyLoading,
    pendingPlots,
    turnActivity,
    turnActivityActive,
    processingConvId,
    serverProcessing,
    sendMessage,
    cancel,
    clearAssistantError,
  } = useChat(activeConvId, token, anthropicApiKey, selectedModel);

  // Auto-select: only when there's no persisted active conversation
  useEffect(() => {
    if (!activeConvId && conversations.length > 0) {
      setActiveConvId(conversations[0].id);
    }
  }, [conversations, activeConvId, setActiveConvId]);

  function handleLogin(accessToken: string, username: string) {
    setToken(accessToken);
    setUser(username);
  }

  function handleLogout() {
    cancel();
    setToken("");
    setUser("");
    setActiveConvId(null);
    localStorage.removeItem("bb_active_conv");
    setAnthropicApiKey("");
  }

  async function handleNewConversation() {
    if (!user) return;
    const id = await create();
    if (id) setActiveConvId(id);
  }

  async function handleDeleteConversation(id: string) {
    await remove(id);
    if (id === activeConvId) {
      const remaining = conversations.filter((c) => c.id !== id);
      setActiveConvId(remaining.length > 0 ? remaining[0].id : null);
    }
  }

  async function handleSend(text: string) {
    let convId = activeConvId;
    if (!convId && user) {
      convId = await create();
      if (convId) setActiveConvId(convId);
    }
    if (!convId) return;
    sendMessage(text, convId).then(() => refresh());
  }

  if (!isLoggedIn) {
    return <LoginPage onLogin={handleLogin} />;
  }

  const sidebarProps = {
    conversations,
    activeId: activeConvId,
    processingId: processingConvId,
    user,
    onSelectConversation: handleSelectConversation,
    onNewConversation: handleNewConversation,
    onDeleteConversation: handleDeleteConversation,
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
          streamingText={streamingText}
          pendingPlots={pendingPlots}
          tokenUsage={tokenUsage}
          historyLoading={historyLoading}
          tpmInputGuideTokens={tpmInputGuideTokens}
          tpmServerSliding60s={tpmServerSliding60s}
          modelContextWindowTokens={modelContextWindowTokens}
          maxInputTokensTarget={maxInputTokensTarget}
          turnActivity={turnActivity}
          turnActivityActive={turnActivityActive}
          serverProcessing={serverProcessing}
          onSend={handleSend}
          onDismissAssistantError={clearAssistantError}
          disabled={false}
          availableModels={availableModels}
          selectedModel={selectedModel ?? defaultModel}
          onSelectModel={handleSelectModel}
          narrowNav={
            isNarrow
              ? {
                onOpenSidebar: () => setSidebarOpen(true),
              }
              : undefined
          }
        />
      </main>
    </div>
  );
}
