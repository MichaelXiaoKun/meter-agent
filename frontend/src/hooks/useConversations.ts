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
  /**
   * True after the first list load for the current user finishes (success or
   * error). Used to sync ``activeConvId`` without clearing an idle selection
   * while the first fetch is still in flight.
   */
  const [listLoaded, setListLoaded] = useState(false);

  const refresh = useCallback(async (): Promise<Conversation[] | undefined> => {
    if (!userId) return undefined;
    setLoading(true);
    try {
      const list = await api.listConversations(userId);
      setConversations(list);
      return list;
    } catch (err) {
      if (!isAbortOrUnload(err))
        console.error("Failed to load conversations:", err);
    } finally {
      setLoading(false);
    }
    return undefined;
  }, [userId]);

  useEffect(() => {
    if (!userId) {
      setListLoaded(false);
      return;
    }
    setListLoaded(false);
    const ac = new AbortController();
    api.listConversations(userId, ac.signal)
      .then(setConversations)
      .catch((err) => {
        if (!isAbortOrUnload(err))
          console.error("Failed to load conversations:", err);
      })
      .finally(() => {
        if (!ac.signal.aborted) setListLoaded(true);
      });
    return () => {
      ac.abort();
    };
  }, [userId]);

  const create = useCallback(async () => {
    if (!userId) return "";
    const id = await api.createConversation(userId);
    await refresh();
    return id;
  }, [userId, refresh]);

  const remove = useCallback(
    async (convId: string): Promise<Conversation[] | undefined> => {
      if (!userId) return undefined;
      await api.deleteConversation(convId, userId);
      return (await refresh()) ?? undefined;
    },
    [userId, refresh]
  );

  const removeMany = useCallback(
    async (convIds: string[]): Promise<Conversation[] | undefined> => {
      if (!userId || convIds.length === 0) return undefined;
      for (const id of convIds) {
        await api.deleteConversation(id, userId);
      }
      return (await refresh()) ?? undefined;
    },
    [userId, refresh]
  );

  const rename = useCallback(
    async (convId: string, title: string) => {
      await api.updateTitle(convId, title);
      await refresh();
    },
    [refresh]
  );

  return {
    conversations,
    loading,
    listLoaded,
    refresh,
    create,
    remove,
    removeMany,
    rename,
  };
}
