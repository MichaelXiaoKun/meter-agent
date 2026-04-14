import { useCallback, useEffect, useState } from "react";
import LoginPage from "./components/LoginPage";
import Sidebar from "./components/Sidebar";
import ChatView from "./components/ChatView";
import { useConversations } from "./hooks/useConversations";
import { useChat } from "./hooks/useChat";

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

export default function App() {
  const [token, setToken] = useLocalStorage("bb_token", "");
  const [user, setUser] = useLocalStorage("bb_user", "");
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

  const { conversations, refresh, create, remove } = useConversations(user);
  const {
    messages,
    status,
    streamingText,
    tokenUsage,
    pendingPlots,
    processingConvId,
    serverProcessing,
    sendMessage,
    cancel,
  } = useChat(activeConvId, token);

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
      <Sidebar
        conversations={conversations}
        activeId={activeConvId}
        processingId={processingConvId}
        tokenUsage={tokenUsage}
        user={user}
        onSelectConversation={setActiveConvId}
        onNewConversation={handleNewConversation}
        onDeleteConversation={handleDeleteConversation}
        onLogout={handleLogout}
      />
      <main className="flex-1">
        <ChatView
          messages={messages}
          status={status}
          streamingText={streamingText}
          pendingPlots={pendingPlots}
          serverProcessing={serverProcessing}
          onSend={handleSend}
          disabled={false}
        />
      </main>
    </div>
  );
}
