import type { Conversation } from "./types";

export type DateBucket = "today" | "yesterday" | "earlier";

export type ListSection = "pinned" | DateBucket;

export type ListItem =
  | { kind: "header"; id: string; section: ListSection }
  | { kind: "row"; id: string; conv: Conversation };

export const CONV_LIST_HEADER_PX = 30;
/** Two-line row + padding; keep in sync with ``ConversationList`` (``py-1`` + title + date). */
export const CONV_LIST_ROW_PX = 64;

function dateBucketForUpdatedAt(ts: number): DateBucket {
  const now = new Date();
  const d = new Date(ts * 1000);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfConv = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round(
    (startOfToday.getTime() - startOfConv.getTime()) / 86_400_000
  );
  if (diffDays < 0) return "earlier";
  if (diffDays === 0) return "today";
  if (diffDays === 1) return "yesterday";
  return "earlier";
}

export function sectionLabel(section: ListSection, locale: string): string {
  const l = locale.toLowerCase();
  if (l.startsWith("zh")) {
    if (section === "pinned") return "已置顶";
    if (section === "today") return "今天";
    if (section === "yesterday") return "昨天";
    return "更早";
  }
  if (section === "pinned") return "Pinned";
  if (section === "today") return "Today";
  if (section === "yesterday") return "Yesterday";
  return "Earlier";
}

/**
 * Pinned (preserving ``pins`` order), then—among unpinned—today / yesterday / earlier,
 * each bucket sorted by ``updated_at`` descending.
 */
export function buildConversationListItems(
  conversations: Conversation[],
  pins: string[],
): ListItem[] {
  const byId = new Map(conversations.map((c) => [c.id, c] as const));
  const pinSet = new Set(pins);
  const items: ListItem[] = [];

  const pinned: Conversation[] = [];
  for (const id of pins) {
    const c = byId.get(id);
    if (c) pinned.push(c);
  }
  if (pinned.length) {
    items.push({ kind: "header", id: "h-pinned", section: "pinned" });
    for (const c of pinned) {
      items.push({ kind: "row", id: c.id, conv: c });
    }
  }

  const rest = conversations.filter((c) => !pinSet.has(c.id));
  const today: Conversation[] = [];
  const yesterday: Conversation[] = [];
  const earlier: Conversation[] = [];
  for (const c of rest) {
    const b = dateBucketForUpdatedAt(c.updated_at);
    if (b === "today") today.push(c);
    else if (b === "yesterday") yesterday.push(c);
    else earlier.push(c);
  }
  const byUpdated = (a: Conversation, b: Conversation) => b.updated_at - a.updated_at;
  today.sort(byUpdated);
  yesterday.sort(byUpdated);
  earlier.sort(byUpdated);

  const pushBlock = (label: DateBucket, list: Conversation[]) => {
    if (list.length === 0) return;
    items.push({ kind: "header", id: `h-${label}`, section: label });
    for (const c of list) {
      items.push({ kind: "row", id: c.id, conv: c });
    }
  };
  pushBlock("today", today);
  pushBlock("yesterday", yesterday);
  pushBlock("earlier", earlier);

  return items;
}

export function itemHeight(it: ListItem): number {
  return it.kind === "header" ? CONV_LIST_HEADER_PX : CONV_LIST_ROW_PX;
}

export function isConversationUnread(
  c: { id: string; updated_at: number },
  readMap: Record<string, number>
): boolean {
  const r = readMap[c.id];
  if (r === undefined) return false;
  return c.updated_at > r;
}
