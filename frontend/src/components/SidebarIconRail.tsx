/**
 * Collapsed layout: logo (opens full sidebar) and new-conversation — no rail chrome.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ThemeToggle from "./ThemeToggle";

/** Matches expanded ``Sidebar`` new-chat icon tile so open ↔ rail feels continuous. */
export const SHELF_NEW_CHAT_TILE_CLASS =
  "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-brand-border/80 bg-white text-brand-700 shadow-sm ring-1 ring-brand-border/40 transition-colors hover:border-brand-500 hover:bg-brand-50 hover:text-brand-900 dark:bg-brand-100 dark:text-brand-muted dark:hover:border-brand-border dark:hover:bg-white/10 dark:hover:text-brand-900";

/** Rounded rectangle with a left rail — dock / hide (expanded) or open-panel hint (rail hover). */
export function IconSidebarDock({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <rect x="4" y="5" width="16" height="14" rx="2" strokeWidth={1.75} />
      <line x1="9.5" y1="5" x2="9.5" y2="19" strokeWidth={1.75} />
    </svg>
  );
}

/** Pencil with writing lines — “compose / new message”. Shared with expanded ``Sidebar``. */
export function IconPencilWriting({ className }: { className?: string }) {
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
        d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L7.5 20.5 3 21l.5-4.5L16.732 3.732z"
      />
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M4 20h4M4 16h7M4 12h5"
        opacity={0.45}
      />
    </svg>
  );
}

function railUserInitial(user: string): string {
  const t = user.trim();
  if (!t) return "?";
  return t[0]!.toLocaleUpperCase();
}

function IconLogout({ className }: { className?: string }) {
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
        d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
      />
    </svg>
  );
}

const MENU_MIN_W = 176;

function clampMenuPoint(x: number, y: number, menuH: number): { x: number; y: number } {
  if (typeof window === "undefined") return { x, y };
  const maxX = window.innerWidth - MENU_MIN_W - 8;
  const maxY = window.innerHeight - menuH - 8;
  return {
    x: Math.max(8, Math.min(x, maxX)),
    y: Math.max(8, Math.min(y, maxY)),
  };
}

interface SidebarIconRailProps {
  onExpand: () => void;
  onNewConversation: () => void;
  /** Logged-in identity — compact initial tile; click opens account menu. */
  user: string;
  onLogout: () => void;
}

export default function SidebarIconRail({
  onExpand,
  onNewConversation,
  user,
  onLogout,
}: SidebarIconRailProps) {
  const initial = railUserInitial(user);
  const accountButtonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);

  const closeMenu = useCallback(() => setMenu(null), []);

  const openMenuNearButton = useCallback(() => {
    const el = accountButtonRef.current;
    if (!el || typeof window === "undefined") return;
    const r = el.getBoundingClientRect();
    const menuH = 88;
    const gap = 6;
    let x = r.right + gap;
    const y = r.top;
    if (x + MENU_MIN_W > window.innerWidth - 8) {
      x = r.left - MENU_MIN_W - gap;
    }
    setMenu(clampMenuPoint(x, y, menuH));
  }, []);

  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeMenu();
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (menuRef.current?.contains(t)) return;
      if (accountButtonRef.current?.contains(t)) return;
      closeMenu();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [menu, closeMenu]);

  const accountTitle = `Signed in as ${user}. Click for account menu.`;

  const menuPortal =
    menu &&
    createPortal(
      <div
        ref={menuRef}
        role="menu"
        aria-label="Account"
        className="fixed z-[300] min-w-[11rem] rounded-xl border border-brand-border bg-white py-1 shadow-xl ring-1 ring-black/5 dark:bg-brand-100 dark:ring-white/10 dark:shadow-[0_16px_48px_-12px_rgba(0,0,0,0.55)]"
        style={{ left: menu.x, top: menu.y }}
      >
        <button
          type="button"
          role="menuitem"
          className="flex w-full px-3 py-2 text-left text-sm text-brand-900 hover:bg-brand-50 dark:hover:bg-white/10"
          onClick={() => {
            closeMenu();
            onExpand();
          }}
        >
          Open sidebar…
        </button>
        <button
          type="button"
          role="menuitem"
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
          onClick={() => {
            closeMenu();
            onLogout();
          }}
        >
          <IconLogout className="h-4 w-4 shrink-0 opacity-80" />
          <span>Sign out</span>
        </button>
      </div>,
      document.body,
    );

  return (
    <nav
      className="flex h-full min-h-0 w-full flex-1 flex-col items-start gap-2.5 bg-gradient-to-b from-white/95 to-brand-100 pl-3 pr-2 pt-[max(0.75rem,env(safe-area-inset-top,0px))] pb-[max(0.75rem,env(safe-area-inset-bottom,0px))] dark:bg-gradient-to-b dark:from-brand-50 dark:to-brand-50"
      aria-label="Conversations and account"
    >
      <button
        type="button"
        onClick={onExpand}
        className="group relative flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center overflow-hidden rounded-xl border border-brand-border/80 bg-white text-brand-700 shadow-sm ring-1 ring-brand-border/40 transition-[border-color,box-shadow,background-color] hover:border-brand-400 hover:bg-brand-50 dark:bg-brand-100 dark:text-brand-muted dark:hover:bg-white/10"
        title="Open conversations"
        aria-label="Open conversations sidebar"
      >
        <span className="relative size-8 shrink-0">
          <img
            src="/api/logo"
            alt=""
            width={32}
            height={32}
            className="absolute inset-0 z-0 size-8 rounded-md object-cover"
          />
          {/* Opaque sheet on hover fully covers the logo; dock sits on top. */}
          <span className="absolute inset-0 z-10 flex items-center justify-center rounded-md bg-white opacity-0 transition-[opacity,background-color] duration-200 ease-out motion-reduce:transition-none motion-reduce:duration-0 group-hover:bg-brand-50 group-hover:opacity-100 dark:bg-brand-100 dark:group-hover:bg-brand-100/95">
            <IconSidebarDock className="h-5 w-5 shrink-0 text-brand-800 transition-colors duration-200 ease-out motion-reduce:transition-none group-hover:text-brand-900 dark:text-brand-muted dark:group-hover:text-brand-900" />
          </span>
        </span>
      </button>

      <button
        type="button"
        onClick={onNewConversation}
        className={SHELF_NEW_CHAT_TILE_CLASS}
        title="New chat"
        aria-label="New chat"
      >
        <IconPencilWriting className="h-5 w-5" />
      </button>

      <div className="mt-auto flex w-full flex-col items-start gap-2.5">
        <ThemeToggle size="sm" />
      <button
        ref={accountButtonRef}
        type="button"
        className={`${SHELF_NEW_CHAT_TILE_CLASS} cursor-pointer text-sm font-semibold text-brand-800 dark:text-brand-muted dark:hover:text-brand-900`}
        title={accountTitle}
        aria-label="Account menu — signed-in user"
        aria-haspopup="menu"
        aria-expanded={menu ? true : false}
        onClick={() => {
          if (menu) closeMenu();
          else openMenuNearButton();
        }}
      >
        <span aria-hidden className="select-none">
          {initial}
        </span>
      </button>
      </div>

      {menuPortal}
    </nav>
  );
}
