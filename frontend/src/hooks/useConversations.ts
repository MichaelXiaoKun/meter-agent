import { useCallback, useEffect, useState } from "react";
import type { Conversation } from "../types";
import * as api from "../api";

function isAbortOrUnload(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof TypeError && /load failed/i.test(err.message)) return true;
  return false;
}

export function useConversations(userId: string) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    try {
      const list = await api.listConversations(userId);
      setConversations(list);
    } catch (err) {
      if (!isAbortOrUnload(err))
        console.error("Failed to load conversations:", err);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    if (!userId) return;
    const ac = new AbortController();
    api.listConversations(userId, ac.signal)
      .then(setConversations)
      .catch((err) => {
        if (!isAbortOrUnload(err))
          console.error("Failed to load conversations:", err);
      });
    return () => ac.abort();
  }, [userId]);

  const create = useCallback(async () => {
    if (!userId) return "";
    const id = await api.createConversation(userId);
    await refresh();
    return id;
  }, [userId, refresh]);

  const remove = useCallback(
    async (convId: string) => {
      if (!userId) return;
      await api.deleteConversation(convId, userId);
      await refresh();
    },
    [userId, refresh]
  );

  return { conversations, loading, refresh, create, remove };
}
