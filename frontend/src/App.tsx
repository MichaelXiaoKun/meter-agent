import { useCallback, useEffect, useState } from "react";
import LoginPage from "./components/LoginPage";
import Sidebar from "./components/Sidebar";
import SidebarIconRail from "./components/SidebarIconRail";
import ChatView from "./components/ChatView";
import { useConversations } from "./hooks/useConversations";
import { useChat } from "./hooks/useChat";
import { fetchOrchestratorConfig } from "./api";

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
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try {
      return localStorage.getItem("bb_sidebar_open") !== "0";
    } catch {
      return true;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem("bb_sidebar_open", sidebarOpen ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [sidebarOpen]);
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
        })
        .catch(() => {});
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
  } = useChat(activeConvId, token, anthropicApiKey);

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

  return (
    <div className="flex h-screen overflow-hidden bg-brand-50">
      <div
        className={`flex shrink-0 overflow-hidden transition-[width] duration-200 ease-out border-r border-brand-border ${
          sidebarOpen ? "w-72" : "w-14"
        }`}
      >
        {sidebarOpen ? (
          <Sidebar
            conversations={conversations}
            activeId={activeConvId}
            processingId={processingConvId}
            user={user}
            onSelectConversation={setActiveConvId}
            onNewConversation={handleNewConversation}
            onDeleteConversation={handleDeleteConversation}
            onRenameConversation={rename}
            onLogout={handleLogout}
            anthropicApiKey={anthropicApiKey}
            onAnthropicApiKeyChange={setAnthropicApiKey}
            anthropicServerConfigured={anthropicServerConfigured}
            onCollapse={() => setSidebarOpen(false)}
          />
        ) : (
          <SidebarIconRail
            onExpand={() => setSidebarOpen(true)}
            onNewConversation={handleNewConversation}
          />
        )}
      </div>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <ChatView
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
        />
      </main>
    </div>
  );
}
