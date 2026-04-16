import { useEffect, useState } from "react";
import type { Conversation } from "../types";

interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  processingId: string | null;
  user: string;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onDeleteConversation: (id: string) => void;
  onRenameConversation: (id: string, title: string) => void | Promise<void>;
  onLogout: () => void;
  /** Stored only in this browser; sent as X-Anthropic-Key on chat requests. */
  anthropicApiKey: string;
  onAnthropicApiKeyChange: (key: string) => void;
  /** From GET /api/config — null until loaded. */
  anthropicServerConfigured: boolean | null;
  /** Hide the sidebar (main pane can show a control to reopen). */
  onCollapse: () => void;
}

function IconChevronLeft({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M15 19l-7-7 7-7"
      />
    </svg>
  );
}

function relativeDate(ts: number): string {
  const now = new Date();
  const d = new Date(ts * 1000);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const convDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = (today.getTime() - convDay.getTime()) / 86_400_000;
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export default function Sidebar({
  conversations,
  activeId,
  processingId,
  user,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  onRenameConversation,
  onLogout,
  anthropicApiKey,
  onAnthropicApiKeyChange,
  anthropicServerConfigured,
  onCollapse,
}: SidebarProps) {
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [keyDraft, setKeyDraft] = useState(anthropicApiKey);

  useEffect(() => {
    if (keyModalOpen) setKeyDraft(anthropicApiKey);
  }, [keyModalOpen, anthropicApiKey]);

  return (
    <aside className="flex h-full w-72 min-w-[18rem] flex-col bg-brand-100">
      <header className="shrink-0 border-b border-brand-border/80 bg-gradient-to-b from-white/95 to-brand-100/80 px-3 pb-3.5 pt-4 shadow-[0_1px_0_0_rgba(15,23,42,0.04)] backdrop-blur-sm">
        <div className="flex items-center gap-2">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white shadow-sm ring-1 ring-brand-border/70">
            <img
              src="/api/logo"
              alt=""
              width={36}
              height={36}
              className="h-9 w-9 rounded-lg object-cover"
            />
          </div>
          <p className="min-w-0 flex-1 text-sm font-semibold leading-tight text-brand-900">
            Conversations
          </p>
          <button
            type="button"
            onClick={onCollapse}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-brand-muted transition-colors hover:bg-white/80 hover:text-brand-900"
            title="Hide sidebar"
            aria-label="Hide conversations sidebar"
          >
            <IconChevronLeft className="h-5 w-5" />
          </button>
        </div>
      </header>

      {/* New conversation */}
      <div className="px-3 pb-2">
        <button
          onClick={onNewConversation}
          className="w-full rounded-lg bg-linear-to-br from-brand-700 to-brand-500 px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90"
        >
          + New conversation
        </button>
      </div>

      {/* Conversation list */}
      <ConversationList
        conversations={conversations}
        activeId={activeId}
        processingId={processingId}
        onSelect={onSelectConversation}
        onDelete={onDeleteConversation}
        onRename={onRenameConversation}
      />

      {/* Account section */}
      <div className="border-t border-brand-border px-4 py-3">
        <div className="mb-2 truncate text-xs text-brand-muted">
          Signed in as <span className="font-medium text-brand-900">{user}</span>
        </div>
        <button
          type="button"
          onClick={() => setKeyModalOpen(true)}
          className="mb-2 w-full rounded-lg border border-brand-border bg-white px-3 py-1.5 text-left text-sm text-brand-900 transition-colors hover:border-brand-400 hover:bg-brand-50"
        >
          <span className="font-medium">Claude API key</span>
          <span className="mt-0.5 block text-xs font-normal text-brand-muted">
            {anthropicApiKey.trim()
              ? "Saved in this browser"
              : anthropicServerConfigured === false
                ? "Required — server has no key"
                : "Optional — uses server key if unset"}
          </span>
        </button>
        <button
          onClick={onLogout}
          className="w-full rounded-lg border border-brand-border bg-white px-3 py-1.5 text-sm text-brand-muted transition-colors hover:border-red-200 hover:bg-red-50 hover:text-red-600"
        >
          Sign out
        </button>
      </div>

      {keyModalOpen && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal
          aria-labelledby="anthropic-key-title"
          onClick={(e) => {
            if (e.target === e.currentTarget) setKeyModalOpen(false);
          }}
        >
          <div
            className="w-full max-w-md rounded-2xl border border-brand-border bg-white p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2
              id="anthropic-key-title"
              className="text-lg font-semibold text-brand-900"
            >
              Anthropic API key
            </h2>
            <p className="mt-2 text-sm text-brand-muted">
              Paste a key from{" "}
              <a
                href="https://console.anthropic.com/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-brand-700 underline"
              >
                console.anthropic.com
              </a>
              . It is kept in this browser only and sent to your assistant server over HTTPS as{" "}
              <code className="rounded bg-brand-50 px-1 text-xs">X-Anthropic-Key</code>. If you leave
              it blank, the server uses <code className="rounded bg-brand-50 px-1 text-xs">ANTHROPIC_API_KEY</code>{" "}
              when set. To remove a saved key, use <strong className="text-brand-800">Clear</strong> below or{" "}
              <strong className="text-brand-800">Sign out</strong> (both wipe the key from this browser).
            </p>
            {anthropicServerConfigured === false && !anthropicApiKey.trim() && (
              <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
                This deployment has no server-side Anthropic key — add your key here to chat.
              </p>
            )}
            <label className="mt-4 block">
              <span className="mb-1 block text-sm font-medium text-brand-900">Secret key</span>
              <input
                type="password"
                autoComplete="off"
                value={keyDraft}
                onChange={(e) => setKeyDraft(e.target.value)}
                placeholder="sk-ant-api03-…"
                className="w-full rounded-xl border border-brand-border bg-brand-50 px-3 py-2 text-sm text-brand-900 outline-none focus:border-brand-500 focus:bg-white"
              />
            </label>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  onAnthropicApiKeyChange(keyDraft.trim());
                  setKeyModalOpen(false);
                }}
                className="rounded-xl bg-brand-700 px-4 py-2 text-sm font-semibold text-white hover:opacity-90"
              >
                Save
              </button>
              <button
                type="button"
                onClick={() => {
                  onAnthropicApiKeyChange("");
                  setKeyDraft("");
                  setKeyModalOpen(false);
                }}
                className="rounded-xl border border-brand-border bg-white px-4 py-2 text-sm text-brand-muted hover:bg-brand-50"
              >
                Clear
              </button>
              <button
                type="button"
                onClick={() => setKeyModalOpen(false)}
                className="rounded-xl px-4 py-2 text-sm text-brand-muted hover:bg-brand-50"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

/* ------------------------------------------------------------------ */

function IconDotsHorizontal({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="currentColor"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <circle cx="6" cy="12" r="1.5" />
      <circle cx="12" cy="12" r="1.5" />
      <circle cx="18" cy="12" r="1.5" />
    </svg>
  );
}

function ConversationList({
  conversations,
  activeId,
  processingId,
  onSelect,
  onDelete,
  onRename,
}: {
  conversations: Conversation[];
  activeId: string | null;
  processingId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void | Promise<void>;
}) {
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);

  useEffect(() => {
    if (!openMenuId) return;
    function handleMousedown(e: MouseEvent) {
      const t = e.target as HTMLElement;
      if (t.closest(`[data-conv-menu-root="${openMenuId}"]`)) return;
      setOpenMenuId(null);
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpenMenuId(null);
    }
    window.addEventListener("mousedown", handleMousedown);
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("mousedown", handleMousedown);
      window.removeEventListener("keydown", handleKey);
    };
  }, [openMenuId]);

  return (
    <div className="relative flex-1 overflow-y-auto px-2">
      {conversations.map((c) => {
        const isActive = c.id === activeId;
        const isBusy = c.id === processingId;
        const menuOpen = openMenuId === c.id;
        const rowClass = isActive
          ? "bg-white font-semibold text-brand-900 shadow-sm"
          : "text-brand-900/80 hover:bg-white/60";

        return (
          <div
            key={c.id}
            className={`group relative mb-0.5 flex rounded-lg ${rowClass}`}
          >
            <button
              type="button"
              onClick={() => {
                setOpenMenuId(null);
                onSelect(c.id);
              }}
              className="min-w-0 flex-1 rounded-lg px-3 py-2 text-left text-sm transition-colors"
            >
              <div
                className="flex items-center gap-1.5 truncate"
                title={c.title || "New conversation"}
              >
                {isBusy && (
                  <span className="inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-brand-500" />
                )}
                <span className="truncate">{c.title || "New conversation"}</span>
              </div>
              <div className="text-xs text-brand-muted">
                {relativeDate(c.updated_at)}
              </div>
            </button>

            <div
              className={`relative flex shrink-0 items-start pt-1 pr-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 ${menuOpen ? "sm:opacity-100" : ""}`}
              data-conv-menu-root={c.id}
            >
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setOpenMenuId((id) => (id === c.id ? null : c.id));
                }}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-brand-muted transition-colors hover:bg-white/80 hover:text-brand-900"
                title="Conversation actions"
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                aria-label="Conversation actions"
              >
                <IconDotsHorizontal className="h-5 w-5" />
              </button>

              {menuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-full z-50 mt-1 min-w-[11rem] rounded-lg border border-brand-border bg-white py-1 shadow-lg"
                >
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50"
                    onClick={() => {
                      setOpenMenuId(null);
                      if (!window.confirm("Delete this conversation?")) return;
                      onDelete(c.id);
                    }}
                  >
                    Delete chat
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center px-3 py-2 text-left text-sm text-brand-900 hover:bg-brand-50"
                    onClick={() => {
                      setOpenMenuId(null);
                      const current = c.title || "New conversation";
                      const next = window.prompt("Rename conversation", current);
                      if (next === null) return;
                      const trimmed = next.trim();
                      if (!trimmed || trimmed === current) return;
                      void Promise.resolve(onRename(c.id, trimmed)).catch(() => {
                        /* errors surfaced by global / fetch handling if any */
                      });
                    }}
                  >
                    Rename title
                  </button>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
