import type { Message, ContentBlock } from "../core/types";

/** Remove ``turn_activity`` blocks for a read-only / public view (hides tool timeline). */
export function stripTurnActivityBlocks(messages: Message[]): Message[] {
  return messages.map((msg) => {
    if (msg.role === "user") return msg;
    const c = msg.content;
    if (typeof c === "string") return msg;
    if (!Array.isArray(c)) return msg;
    const filtered = c.filter(
      (b) => b && (b as ContentBlock).type !== "turn_activity",
    ) as ContentBlock[];
    if (filtered.length === c.length) return msg;
    if (filtered.length === 0) {
      return { ...msg, content: "" };
    }
    return { ...msg, content: filtered };
  });
}
