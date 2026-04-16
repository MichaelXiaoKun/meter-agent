import { useEffect, useRef, useState } from "react";
import type { Conversation } from "../types";

interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  processingId: string | null;
  user: string;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onDeleteConversation: (id: string) => void;
  onLogout: () => void;
  /** Stored only in this browser; sent as X-Anthropic-Key on chat requests. */
  anthropicApiKey: string;
  onAnthropicApiKeyChange: (key: string) => void;
  /** From GET /api/config — null until loaded. */
  anthropicServerConfigured: boolean | null;
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
  onLogout,
  anthropicApiKey,
  onAnthropicApiKeyChange,
  anthropicServerConfigured,
}: SidebarProps) {
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [keyDraft, setKeyDraft] = useState(anthropicApiKey);

  useEffect(() => {
    if (keyModalOpen) setKeyDraft(anthropicApiKey);
  }, [keyModalOpen, anthropicApiKey]);

  return (
    <aside className="flex h-full w-72 flex-col border-r border-brand-border bg-brand-100">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 pt-4 pb-3">
        <img
          src="/api/logo"
          alt="bluebot"
          className="h-8 w-8 rounded-lg object-cover"
        />
        <span className="text-base font-bold text-brand-900">bluebot</span>
      </div>

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
              when set.
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

interface CtxMenu {
  convId: string;
  x: number;
  y: number;
}

function ConversationList({
  conversations,
  activeId,
  processingId,
  onSelect,
  onDelete,
}: {
  conversations: Conversation[];
  activeId: string | null;
  processingId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [ctx, setCtx] = useState<CtxMenu | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ctx) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && menuRef.current.contains(e.target as Node)) return;
      setCtx(null);
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setCtx(null);
    }
    window.addEventListener("mousedown", handleClick);
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("mousedown", handleClick);
      window.removeEventListener("keydown", handleKey);
    };
  }, [ctx]);

  return (
    <div className="relative flex-1 overflow-y-auto px-2">
      {conversations.map((c) => {
        const isActive = c.id === activeId;
        const isBusy = c.id === processingId;
        return (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            onContextMenu={(e) => {
              e.preventDefault();
              setCtx({ convId: c.id, x: e.clientX, y: e.clientY });
            }}
            className={`w-full rounded-lg px-3 py-2 text-left text-sm transition-colors ${isActive
                ? "bg-white font-semibold text-brand-900 shadow-sm"
                : "text-brand-900/80 hover:bg-white/60"
              }`}
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
        );
      })}

      {ctx && (
        <div
          ref={menuRef}
          style={{ position: "fixed", left: ctx.x, top: ctx.y }}
          className="z-50 min-w-[140px] rounded-lg border border-brand-border bg-white py-1 shadow-lg"
        >
          <button
            onClick={() => {
              onDelete(ctx.convId);
              setCtx(null);
            }}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50"
          >
            <svg
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
              />
            </svg>
            Delete
          </button>
        </div>
      )}
    </div>
  );
}
