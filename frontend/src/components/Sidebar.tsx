import { useEffect, useLayoutEffect, useState } from "react";
import type { Conversation } from "../types";
import { IconPencilWriting, IconSidebarDock } from "./SidebarIconRail";
import ThemeToggle from "./ThemeToggle";

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
  /**
   * Desktop shelf mid-collapse: strip the scrollable conversation list, account
   * block, and the “New chat” **label** so the column can shrink without
   * clipped text — only header + icon new-chat remain until the rail swap.
   */
  collapseShelfBody?: boolean;
  /** Desktop shelf: fade list / footer / “New chat” label before ``collapseShelfBody``. */
  collapseShelfFading?: boolean;
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
  collapseShelfBody = false,
  collapseShelfFading = false,
}: SidebarProps) {
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [keyDraft, setKeyDraft] = useState(anthropicApiKey);
  /** Full “New chat” shell grows from rail-sized icon capsule (desktop expand / mount). */
  const [newChatShellOpen, setNewChatShellOpen] = useState(() =>
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? true
      : false,
  );

  useLayoutEffect(() => {
    if (collapseShelfBody) {
      setNewChatShellOpen(false);
      return;
    }
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setNewChatShellOpen(true);
      return;
    }
    setNewChatShellOpen(false);
    const id = requestAnimationFrame(() => {
      requestAnimationFrame(() => setNewChatShellOpen(true));
    });
    return () => cancelAnimationFrame(id);
  }, [collapseShelfBody]);

  /** Collapse: shrink the New chat shell in lockstep with list/footer fade (same DOM as expand). */
  useEffect(() => {
    if (!collapseShelfFading) return;
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return;
    setNewChatShellOpen(false);
  }, [collapseShelfFading]);

  useEffect(() => {
    if (keyModalOpen) setKeyDraft(anthropicApiKey);
  }, [keyModalOpen, anthropicApiKey]);

  const showWideNewChatShell = newChatShellOpen && !collapseShelfBody;
  const showCollapseControl = !collapseShelfFading && !collapseShelfBody;

  return (
    <aside className="flex h-full min-h-0 w-full min-w-0 max-w-full flex-col bg-gradient-to-b from-white/95 to-brand-100 dark:bg-gradient-to-b dark:from-brand-50 dark:to-brand-50">
      {/*
        ChatGPT-style layout (logo + dock, then pill “New chat”) with the
        existing brand palette — no slate/neutral takeover of the sidebar.
        Shelf surface matches narrow rail (same gradient) so open ↔ collapsed
        reads as one continuous brightness.
      */}
      <header className="shrink-0 border-b border-brand-border/80 bg-transparent px-3 pb-3 pt-[max(0.75rem,env(safe-area-inset-top,0px))] shadow-[0_1px_0_0_rgba(15,23,42,0.04)] dark:shadow-[0_1px_0_0_rgba(0,0,0,0.35)]">
        <div
          className={`flex items-center gap-2 ${showCollapseControl ? "justify-between" : "justify-start"}`}
        >
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/90 shadow-sm ring-1 ring-brand-border/60 dark:bg-brand-100/90">
            <img
              src="/api/logo"
              alt=""
              width={32}
              height={32}
              className="h-8 w-8 rounded-md object-cover"
            />
          </div>
          {showCollapseControl && (
            <button
              type="button"
              onClick={onCollapse}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-transparent text-brand-muted/45 shadow-sm ring-1 ring-transparent transition-[color,background-color,border-color,box-shadow,ring-color] hover:border-brand-border/80 hover:bg-white hover:text-brand-800 hover:shadow-sm hover:ring-brand-border/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-brand-50 dark:hover:bg-white/10 dark:hover:text-brand-900 dark:focus-visible:ring-offset-brand-100"
              title="Close sidebar"
              aria-label="Close conversations sidebar"
            >
              <IconSidebarDock className="h-5 w-5 shrink-0" />
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={onNewConversation}
          title="New chat"
          aria-label="New chat"
          className={`group mt-2.5 self-start box-border flex min-w-0 items-center overflow-hidden rounded-xl border border-brand-border/80 bg-white text-left text-[0.9375rem] font-normal text-brand-900 shadow-sm ring-1 ring-brand-border/40 transition-[width,height,max-width,min-height,gap,padding] duration-200 ease-[cubic-bezier(0.25,0.46,0.45,0.94)] motion-reduce:transition-none motion-reduce:duration-0 hover:border-brand-500 hover:bg-brand-50 dark:bg-brand-100 ${showWideNewChatShell
              ? "h-auto min-h-9 w-full max-w-full gap-2.5 px-2.5 py-1"
              : "h-9 w-9 max-w-9 shrink-0 justify-center gap-0 px-0 py-0"
            }`}
        >
          <span
            className={`flex shrink-0 items-center justify-center text-brand-700 group-hover:text-brand-900 dark:text-brand-muted dark:group-hover:text-brand-900 ${showWideNewChatShell ? "h-9 w-9" : "h-full w-full"}`}
          >
            <IconPencilWriting className="h-5 w-5 shrink-0" />
          </span>
          <span
            className={`truncate text-[0.9375rem] ease-out motion-reduce:transition-none ${showWideNewChatShell && !collapseShelfFading
                ? "max-w-[min(11rem,calc(100%-2.75rem))] opacity-100 transition-[max-width,opacity] duration-200 delay-50 motion-reduce:delay-0"
                : "pointer-events-none max-w-0 overflow-hidden opacity-0 transition-[max-width,opacity] duration-200 ease-out motion-reduce:duration-0"
              }`}
          >
            New chat
          </span>
        </button>
      </header>

      {!collapseShelfBody && (
        <div
          className={`flex min-h-0 flex-1 flex-col transition-opacity duration-200 ease-out motion-reduce:transition-none motion-reduce:duration-0 ${collapseShelfFading ? "pointer-events-none opacity-0" : "opacity-100"}`}
        >
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
          <div className="shrink-0 border-t border-brand-border px-4 pb-[max(0.75rem,env(safe-area-inset-bottom,0px))] pt-3">
            <div className="mb-2 truncate text-xs text-brand-muted">
              Signed in as <span className="font-medium text-brand-900">{user}</span>
            </div>
            <div className="mb-2 hidden items-center justify-between gap-2 lg:flex">
              <span className="text-xs text-brand-muted">Appearance</span>
              <ThemeToggle size="sm" />
            </div>
            <button
              type="button"
              onClick={() => setKeyModalOpen(true)}
              className="mb-2 w-full rounded-lg border border-brand-border bg-white px-3 py-1.5 text-left text-sm text-brand-900 transition-colors hover:border-brand-400 hover:bg-brand-50 dark:bg-brand-100/90 dark:hover:border-brand-border dark:hover:bg-white/10"
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
              className="w-full rounded-lg border border-brand-border bg-white px-3 py-1.5 text-sm text-brand-muted transition-colors hover:border-red-200 hover:bg-red-50 hover:text-red-600 dark:bg-brand-100/90 dark:hover:border-red-900/50 dark:hover:bg-red-950/35 dark:hover:text-red-400"
            >
              Sign out
            </button>
          </div>
        </div>
      )}

      {keyModalOpen && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4 dark:bg-black/60"
          role="dialog"
          aria-modal
          aria-labelledby="anthropic-key-title"
          onClick={(e) => {
            if (e.target === e.currentTarget) setKeyModalOpen(false);
          }}
        >
          <div
            className="w-full max-w-md rounded-2xl border border-brand-border bg-white p-5 shadow-xl dark:bg-brand-100 dark:shadow-[0_24px_64px_-24px_rgba(0,0,0,0.55)]"
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
              <p className="mt-2 rounded-lg border border-amber-200/80 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/40 dark:text-amber-100">
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
                className="w-full rounded-xl border border-brand-border bg-brand-50 px-3 py-2 text-sm text-brand-900 outline-none focus:border-brand-500 focus:bg-white dark:focus:bg-brand-100"
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
                className="rounded-xl border border-brand-border bg-white px-4 py-2 text-sm text-brand-muted hover:bg-brand-50 dark:hover:bg-brand-100/80"
              >
                Clear
              </button>
              <button
                type="button"
                onClick={() => setKeyModalOpen(false)}
                className="rounded-xl px-4 py-2 text-sm text-brand-muted hover:bg-brand-50 dark:hover:bg-white/10"
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
          ? "bg-white font-semibold text-brand-900 shadow-sm dark:bg-white/10 dark:shadow-[0_1px_0_0_rgba(0,0,0,0.25)]"
          : "text-brand-900/80 hover:bg-white/60 dark:hover:bg-white/10";

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
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-brand-muted transition-colors hover:bg-white/80 hover:text-brand-900 dark:text-brand-muted/90 dark:hover:bg-white/10 dark:hover:text-brand-900"
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
                  className="absolute right-0 top-full z-50 mt-1 min-w-[11rem] rounded-lg border border-brand-border bg-white py-1 shadow-lg dark:bg-brand-100 dark:shadow-[0_12px_40px_-12px_rgba(0,0,0,0.5)]"
                >
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
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
                    className="flex w-full items-center px-3 py-2 text-left text-sm text-brand-900 hover:bg-brand-50 dark:hover:bg-white/10"
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
