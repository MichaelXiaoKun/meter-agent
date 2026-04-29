import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useConversationListPrefs } from "../hooks/useConversationListPrefs";
import type { Conversation } from "../types";
import BluebotWordmarkLogo from "./BluebotWordmarkLogo";
import ConversationList from "./ConversationList";
import { IconPencilWriting, IconSidebarDock } from "./SidebarIconRail";
import ThemeToggle from "./ThemeToggle";

/** Multi-select: checklist affordance. */
function IconListMultiselect({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="3" y="4" width="5" height="5" rx="1" />
      <line x1="12" y1="6.5" x2="20" y2="6.5" />
      <rect x="3" y="10" width="5" height="5" rx="1" />
      <line x1="12" y1="12.5" x2="20" y2="12.5" />
      <rect x="3" y="16" width="5" height="5" rx="1" />
      <line x1="12" y1="18.5" x2="20" y2="18.5" />
    </svg>
  );
}

function IconCheck({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M6 12l4 4 8-9" />
    </svg>
  );
}

interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  processingId: string | null;
  user: string;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onDeleteConversation: (id: string) => void;
  /** Delete many in one round-trip (list refresh once at end). */
  onDeleteConversations: (ids: string[]) => void | Promise<void>;
  onRenameConversation: (id: string, title: string) => void | Promise<void>;
  onLogout: () => void;
  /** Stored only in this browser; sent as X-Anthropic-Key on chat requests. */
  anthropicApiKey: string;
  onAnthropicApiKeyChange: (key: string) => void;
  /** From GET /api/config — null until loaded. */
  anthropicServerConfigured: boolean | null;
  showApiKeyControl?: boolean;
  accountLabel?: string;
  logoutLabel?: string;
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

export default function Sidebar({
  conversations,
  activeId,
  processingId,
  user,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  onDeleteConversations,
  onRenameConversation,
  onLogout,
  anthropicApiKey,
  onAnthropicApiKeyChange,
  anthropicServerConfigured,
  showApiKeyControl = true,
  accountLabel = "Signed in as",
  logoutLabel = "Sign out",
  onCollapse,
  collapseShelfBody = false,
  collapseShelfFading = false,
}: SidebarProps) {
  const { pins, readMap, togglePin, markRead } = useConversationListPrefs(
    user,
    conversations
  );
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [keyModalOpen, setKeyModalOpen] = useState(false);

  useEffect(() => {
    if (!selectMode) setSelectedIds([]);
  }, [selectMode]);

  useEffect(() => {
    const valid = new Set(conversations.map((c) => c.id));
    setSelectedIds((s) => s.filter((id) => valid.has(id)));
  }, [conversations]);

  const toggleSelect = (id: string) => {
    setSelectedIds((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  };
  const [keyDraft, setKeyDraft] = useState(anthropicApiKey);
  /** Full “New chat” shell: wide when the list strip is already visible (no flash on first paint). */
  const [newChatShellOpen, setNewChatShellOpen] = useState(() => {
    if (typeof window === "undefined") {
      return !collapseShelfBody;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return true;
    }
    return !collapseShelfBody;
  });
  /**
   * Only play the “New chat” width expand animation when the shelf list **was
   * stripped** (``collapseShelfBody``) and is now open again. On first paint
   * with the shelf already open (page refresh, desktop wide bar), do **not** run
   * the false → rAF → true sequence, which produced a visible “grow from icon”.
   */
  const prevBodyStripped = useRef(collapseShelfBody);
  useLayoutEffect(() => {
    if (collapseShelfBody) {
      setNewChatShellOpen(false);
      prevBodyStripped.current = true;
      return;
    }
    const wasStripped = prevBodyStripped.current;
    prevBodyStripped.current = false;
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setNewChatShellOpen(true);
      return;
    }
    if (wasStripped) {
      setNewChatShellOpen(false);
      const id = requestAnimationFrame(() => {
        requestAnimationFrame(() => setNewChatShellOpen(true));
      });
      return () => cancelAnimationFrame(id);
    }
    setNewChatShellOpen(true);
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
      <header className="shrink-0 bg-transparent px-2.5 pb-1.5 pt-[max(0.75rem,env(safe-area-inset-top,0px))]">
        <div
          className={`flex items-center gap-2 ${showCollapseControl ? "justify-between" : "justify-start"}`}
        >
          {showCollapseControl ? (
            <div className="flex min-w-0 flex-1 items-center pl-1">
              <BluebotWordmarkLogo
                scaleLikeSaaS={false}
                className="h-6 w-auto max-w-[min(9.25rem,100%)] text-brand-900 dark:text-brand-muted"
              />
            </div>
          ) : (
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/90 shadow-sm ring-1 ring-brand-border/60 dark:bg-brand-100/90">
              <IconSidebarDock className="h-5 w-5 shrink-0 text-brand-800 dark:text-brand-muted" />
            </div>
          )}
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
          className={`group mt-2.5 self-start box-border flex min-w-0 items-center overflow-hidden rounded-xl border border-transparent bg-transparent text-left text-[0.9375rem] font-normal text-brand-900 shadow-none ring-1 ring-transparent transition-[width,height,max-width,min-height,gap,padding,background-color,border-color,box-shadow,ring-color] duration-200 ease-[cubic-bezier(0.25,0.46,0.45,0.94)] motion-reduce:transition-none motion-reduce:duration-0 hover:border-brand-500 hover:bg-brand-50 hover:shadow-sm hover:ring-brand-border/40 dark:hover:bg-brand-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-brand-50 dark:focus-visible:ring-offset-brand-100 ${showWideNewChatShell
            ? "h-auto min-h-9 w-full max-w-full gap-1 py-1 pl-0 pr-2"
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
              ? "max-w-[min(11rem,calc(100%-2.5rem))] opacity-100 transition-[max-width,opacity] duration-200 delay-50 motion-reduce:delay-0"
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
          <div className="flex shrink-0 items-center justify-between gap-2 pb-1.5 pt-1 pl-2.5 pr-2">
            <button
              type="button"
              onClick={() => setSelectMode((m) => !m)}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-transparent text-brand-muted transition-[color,background-color,border-color,box-shadow] hover:border-brand-border/80 hover:bg-white hover:text-brand-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 dark:hover:bg-white/10 dark:hover:text-brand-900"
              title={selectMode ? "Done selecting" : "Select conversations to delete"}
              aria-label={selectMode ? "Done selecting conversations" : "Select multiple conversations"}
            >
              {selectMode ? (
                <IconCheck className="h-5 w-5" />
              ) : (
                <IconListMultiselect className="h-5 w-5" />
              )}
            </button>
            {selectMode && selectedIds.length > 0 && (
              <span className="text-xs tabular-nums text-brand-muted">
                {selectedIds.length} selected
              </span>
            )}
          </div>
          {/* Conversation list (grouped, pins, virtualized) */}
          <ConversationList
            conversations={conversations}
            activeId={activeId}
            processingId={processingId}
            pins={pins}
            readMap={readMap}
            selectionMode={selectMode}
            selectedIds={selectedIds}
            onToggleSelect={toggleSelect}
            onExitSelectMode={() => {
              setSelectMode(false);
              setSelectedIds([]);
            }}
            onSelect={onSelectConversation}
            onDelete={onDeleteConversation}
            onRename={onRenameConversation}
            onTogglePin={togglePin}
            onMarkRead={markRead}
          />

          {selectMode && (
            <div className="shrink-0 space-y-1.5 px-2.5 py-2">
              <button
                type="button"
                disabled={selectedIds.length === 0}
                onClick={async () => {
                  if (selectedIds.length === 0) return;
                  if (
                    !window.confirm(
                      `Delete ${selectedIds.length} conversation(s)? This cannot be undone.`
                    )
                  ) {
                    return;
                  }
                  const snapshot = [...selectedIds];
                  setSelectMode(false);
                  setSelectedIds([]);
                  await onDeleteConversations(snapshot);
                }}
                className="w-full rounded-lg bg-red-600 py-1.5 text-sm font-medium text-white shadow-sm transition-opacity hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {selectedIds.length > 0
                  ? `Delete ${selectedIds.length} conversation${selectedIds.length === 1 ? "" : "s"}`
                  : "Delete selected"}
              </button>
            </div>
          )}

          {/* Account section */}
          <div className="shrink-0 px-4 pb-[max(0.75rem,env(safe-area-inset-bottom,0px))] pt-3">
            <div className="mb-2 truncate text-xs text-brand-muted">
              {accountLabel} <span className="font-medium text-brand-900">{user}</span>
            </div>
            <div className="mb-2 hidden items-center justify-between gap-2 lg:flex">
              <span className="text-xs text-brand-muted">Appearance</span>
              <ThemeToggle size="sm" />
            </div>
            {showApiKeyControl && (
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
            )}
            <button
              onClick={onLogout}
              className="w-full rounded-lg border border-brand-border bg-white px-3 py-1.5 text-sm text-brand-muted transition-colors hover:border-red-200 hover:bg-red-50 hover:text-red-600 dark:bg-brand-100/90 dark:hover:border-red-900/50 dark:hover:bg-red-950/35 dark:hover:text-red-400"
            >
              {logoutLabel}
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
