import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type Dispatch,
  type SetStateAction,
} from "react";
import { motion, AnimatePresence } from "framer-motion";
import { formatConversationRelativeDate, getDefaultDateLocale } from "../conversationRelativeDate";
import {
  buildConversationListItems,
  isConversationUnread,
  sectionLabel,
  type ListItem,
} from "../conversationListModel";
import { useListVirtualWindow } from "../hooks/useListVirtualWindow";
import type { Conversation } from "../types";

function IconDotsHorizontal({ className }: { className?: string }) {
  return (
    <svg className={className} fill="currentColor" viewBox="0 0 24 24" aria-hidden>
      <circle cx="6" cy="12" r="1.5" />
      <circle cx="12" cy="12" r="1.5" />
      <circle cx="18" cy="12" r="1.5" />
    </svg>
  );
}

function IconPin({ className, filled }: { className?: string; filled?: boolean }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden>
      {filled ? (
        <path
          fill="currentColor"
          d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5A2.5 2.5 0 1 1 12 6a2.5 2.5 0 0 1 0 5.5z"
        />
      ) : (
        <g fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 21s7-4.35 7-11a7 7 0 0 0-14 0c0 6.65 7 11 7 11z" />
          <circle cx="12" cy="10" r="3" />
        </g>
      )}
    </svg>
  );
}

interface ConversationListProps {
  conversations: Conversation[];
  activeId: string | null;
  processingId: string | null;
  pins: string[];
  readMap: Record<string, number>;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void | Promise<void>;
  onTogglePin: (id: string) => void;
  onMarkRead: (id: string, updatedAt: number) => void;
  /** Multi-select: tap rows to check, then use sidebar “Delete n”. */
  selectionMode?: boolean;
  selectedIds?: string[];
  onToggleSelect?: (id: string) => void;
  onExitSelectMode?: () => void;
}

export default function ConversationList({
  conversations,
  activeId,
  processingId,
  pins,
  readMap,
  onSelect,
  onDelete,
  onRename,
  onTogglePin,
  onMarkRead,
  selectionMode = false,
  selectedIds = [],
  onToggleSelect = () => {},
  onExitSelectMode = () => {},
}: ConversationListProps) {
  const locale = getDefaultDateLocale();
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const parentRef = useRef<HTMLDivElement>(null);

  const listItems: ListItem[] = useMemo(
    () => buildConversationListItems(conversations, pins),
    [conversations, pins]
  );
  const { start, end, totalSize, getOffset, rowHeights } = useListVirtualWindow(
    listItems,
    parentRef,
    8
  );

  useLayoutEffect(() => {
    if (!activeId) return;
    const c = conversations.find((x) => x.id === activeId);
    if (c) onMarkRead(c.id, c.updated_at);
  }, [activeId, conversations, onMarkRead]);

  useLayoutEffect(() => {
    if (selectionMode) setOpenMenuId(null);
  }, [selectionMode]);

  useEffect(() => {
    if (!selectionMode) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onExitSelectMode();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectionMode, onExitSelectMode]);

  useLayoutEffect(() => {
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

  if (listItems.length === 0) {
    return (
      <div className="px-2.5 py-6 text-center text-xs text-brand-muted">
        No conversations yet
      </div>
    );
  }

  return (
    <div
      ref={parentRef}
      className="relative min-h-0 min-w-0 flex-1 overflow-y-auto px-2.5 [scrollbar-gutter:stable]"
    >
      <div className="relative w-full" style={{ height: totalSize }}>
        {Array.from({ length: Math.max(0, end - start + 1) }, (_, j) => {
          const i = start + j;
          const it = listItems[i];
          if (!it) return null;
          const top = getOffset(i);
          const h = rowHeights[i] ?? 0;
          const pos: CSSProperties = {
            position: "absolute",
            top,
            left: 0,
            right: 0,
            height: h,
          };
          if (it.kind === "header") {
            return (
              <div key={it.id} className="pointer-events-none z-[1] px-0.5" style={pos}>
                <div className="flex h-full flex-col justify-end pb-1 pr-1">
                  <span className="text-[0.65rem] font-semibold uppercase tracking-wider text-brand-muted/90">
                    {sectionLabel(it.section, locale)}
                  </span>
                </div>
              </div>
            );
          }
          return (
            <div key={it.id} style={pos} className="box-border h-full min-h-0 min-w-0 px-0">
              <ConversationRow
                c={it.conv}
                isActive={it.conv.id === activeId}
                isBusy={it.conv.id === processingId}
                isUnread={isConversationUnread(it.conv, readMap)}
                isPinned={pins.includes(it.conv.id)}
                isSelected={selectedIds.includes(it.conv.id)}
                selectionMode={selectionMode}
                locale={locale}
                openMenuId={openMenuId}
                setOpenMenuId={setOpenMenuId}
                onSelect={onSelect}
                onDelete={onDelete}
                onRename={onRename}
                onTogglePin={onTogglePin}
                onToggleSelect={onToggleSelect}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ConversationRow({
  c,
  isActive,
  isBusy,
  isUnread,
  isPinned,
  isSelected,
  selectionMode,
  locale,
  openMenuId,
  setOpenMenuId,
  onSelect,
  onDelete,
  onRename,
  onTogglePin,
  onToggleSelect,
}: {
  c: Conversation;
  isActive: boolean;
  isBusy: boolean;
  isUnread: boolean;
  isPinned: boolean;
  isSelected: boolean;
  selectionMode: boolean;
  locale: string;
  openMenuId: string | null;
  setOpenMenuId: Dispatch<SetStateAction<string | null>>;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void | Promise<void>;
  onTogglePin: (id: string) => void;
  onToggleSelect: (id: string) => void;
}) {
  const menuOpen = openMenuId === c.id;
  const rowClass = isActive
    ? "bg-white font-medium text-brand-900 shadow-sm dark:bg-white/10 dark:shadow-[0_1px_0_0_rgba(0,0,0,0.25)]"
    : isUnread
      ? "bg-brand-50/90 font-medium text-brand-900 dark:bg-sky-950/35 dark:text-brand-900"
      : "text-brand-900/80 hover:bg-white/60 dark:hover:bg-white/10";
  const selectRing =
    selectionMode && isSelected ? "ring-2 ring-brand-500/70 ring-inset" : "";
  const ringBusy =
    isBusy
      ? "ring-1 ring-inset ring-brand-500/40"
      : isUnread
        ? "ring-1 ring-inset ring-sky-400/30 dark:ring-sky-500/25"
        : "";
  const accent = isBusy ? "border-l-[3px] border-l-brand-500" : "";

  return (
    <div
      className={`group relative mb-px box-border flex h-full min-h-0 w-full min-w-0 items-stretch rounded-lg transition-colors duration-150 ${rowClass} ${ringBusy} ${accent} ${selectRing}`}
    >
      <div className="flex min-h-0 min-w-0 flex-1">
        {selectionMode && (
          <div className="flex shrink-0 items-center pl-0.5">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 cursor-pointer rounded border-brand-border text-brand-600 focus:ring-brand-500/40 dark:border-brand-border dark:text-brand-500"
              checked={isSelected}
              onChange={() => onToggleSelect(c.id)}
              onClick={(e) => e.stopPropagation()}
              title="Select to delete"
              aria-label={
                c.title.trim()
                  ? `Select “${c.title}” for deletion`
                  : "Select this conversation for deletion"
              }
            />
          </div>
        )}
        <button
          type="button"
          onClick={() => {
            if (selectionMode) {
              onToggleSelect(c.id);
            } else {
              setOpenMenuId(null);
              onSelect(c.id);
            }
          }}
          className={`flex min-h-0 min-w-0 flex-1 touch-manipulation flex-col items-stretch rounded-lg py-1 pr-1 text-left text-sm leading-tight transition-colors [overflow-y:visible] [overflow-x:clip] ${selectionMode ? "pl-1.5" : "pl-2.5"}`}
        >
        <div
          className="flex min-w-0 items-center gap-1"
          title={c.title || "New conversation"}
        >
          {isBusy && (
            <span
              className="inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-brand-500 shadow-[0_0_0_1px_rgba(59,130,246,0.35)]"
              title="Streaming"
            />
          )}
          {isPinned && (
            <IconPin className="h-3.5 w-3.5 shrink-0 text-brand-600/80 dark:text-brand-400/90" filled />
          )}
          {isUnread && !isBusy && (
            <span
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-sky-500"
              title="Updated"
            />
          )}
          <span
            className={`min-w-0 flex-1 truncate text-left ${isUnread ? "font-semibold" : ""}`}
          >
            {c.title || "New conversation"}
          </span>
        </div>
        <div className="mt-px flex min-h-[1.0625rem] items-center gap-1 text-xs leading-tight text-brand-muted [overflow:visible]">
          {isBusy && <span className="font-medium text-brand-600">Running…</span>}
          <span className="shrink-0">{formatConversationRelativeDate(c.updated_at, locale)}</span>
        </div>
      </button>
      </div>

      {!selectionMode && (
        <div
          className={`relative flex shrink-0 items-start pt-0.5 pr-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 ${menuOpen ? "sm:opacity-100" : ""}`}
          data-conv-menu-root={c.id}
        >
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setOpenMenuId((prev) => (prev === c.id ? null : c.id));
          }}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-brand-muted transition-colors hover:bg-white/80 hover:text-brand-900 dark:text-brand-muted/90 dark:hover:bg-white/10 dark:hover:text-brand-900"
          title="Conversation actions"
          aria-expanded={menuOpen}
          aria-haspopup="menu"
          aria-label="Conversation actions"
        >
          <IconDotsHorizontal className="h-5 w-5" />
        </button>

        <AnimatePresence>
          {menuOpen && (
            <motion.div
              role="menu"
              className="absolute right-0 top-full z-50 mt-1 min-w-[12rem] rounded-lg border border-brand-border bg-white py-1 text-sm shadow-lg dark:bg-brand-100 dark:shadow-[0_12px_40px_-12px_rgba(0,0,0,0.5)]"
              initial={{ opacity: 0, scale: 0.95, y: -4 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: -4 }}
              transition={{ duration: 0.15 }}
            >
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-brand-900 transition-colors active:scale-[0.97] active:transition-transform hover:bg-brand-50 dark:hover:bg-white/10"
              onClick={() => {
                setOpenMenuId(null);
                onTogglePin(c.id);
              }}
            >
              <IconPin className="h-4 w-4" filled={isPinned} />
              {isPinned ? "Unpin" : "Pin to top"}
            </button>
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center px-3 py-2 text-left text-sm text-red-600 transition-colors active:scale-[0.97] active:transition-transform hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
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
              className="flex w-full items-center px-3 py-2 text-left text-sm text-brand-900 transition-colors active:scale-[0.97] active:transition-transform hover:bg-brand-50 dark:hover:bg-white/10"
              onClick={() => {
                setOpenMenuId(null);
                const current = c.title || "New conversation";
                const next = window.prompt("Rename conversation", current);
                if (next === null) return;
                const trimmed = next.trim();
                if (!trimmed || trimmed === current) return;
                void Promise.resolve(onRename(c.id, trimmed)).catch(() => { });
              }}
            >
              Rename title
            </button>
            </motion.div>
          )}
        </AnimatePresence>
        </div>
      )}
    </div>
  );
}
