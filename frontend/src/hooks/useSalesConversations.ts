import { useCallback, useEffect, useRef, useState } from "react";
import type { Conversation } from "../core/types";
import * as api from "../api/client";

const SALES_CONV_IDS_KEY = "bb_sales_conv_ids";
const SALES_ACTIVE_CONV_KEY = "bb_sales_active_conv";
const LEGACY_SALES_CONV_KEY = "bb_sales_conv";

function isAbortOrUnload(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof TypeError && /load failed/i.test(err.message)) return true;
  return false;
}

function readIds(): string[] {
  try {
    const raw = localStorage.getItem(SALES_CONV_IDS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    const ids = Array.isArray(parsed)
      ? parsed.filter((id): id is string => typeof id === "string" && !!id.trim())
      : [];
    const active = localStorage.getItem(SALES_ACTIVE_CONV_KEY);
    const legacy = localStorage.getItem(LEGACY_SALES_CONV_KEY);
    for (const id of [active, legacy]) {
      if (id && !ids.includes(id)) ids.unshift(id);
    }
    return ids;
  } catch {
    try {
      const ids = [
        localStorage.getItem(SALES_ACTIVE_CONV_KEY),
        localStorage.getItem(LEGACY_SALES_CONV_KEY),
      ].filter((id): id is string => typeof id === "string" && !!id.trim());
      return Array.from(new Set(ids));
    } catch {
      return [];
    }
  }
}

function writeIds(ids: string[]) {
  const deduped = Array.from(new Set(ids.filter(Boolean)));
  try {
    localStorage.setItem(SALES_CONV_IDS_KEY, JSON.stringify(deduped));
    if (deduped[0]) localStorage.setItem(LEGACY_SALES_CONV_KEY, deduped[0]);
  } catch {
    /* ignore */
  }
}

function rememberId(id: string) {
  writeIds([id, ...readIds().filter((x) => x !== id)]);
}

function forgetIds(ids: string[]) {
  const doomed = new Set(ids);
  writeIds(readIds().filter((id) => !doomed.has(id)));
}

export function useSalesConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(false);
  const [listLoaded, setListLoaded] = useState(false);
  const requestSeqRef = useRef(0);

  const refresh = useCallback(async (): Promise<Conversation[] | undefined> => {
    const seq = ++requestSeqRef.current;
    const ids = readIds();
    setLoading(true);
    try {
      const list = await api.listSalesConversations(ids);
      if (seq !== requestSeqRef.current) return undefined;
      setConversations(list);
      writeIds(list.map((c) => c.id));
      return list;
    } catch (err) {
      if (!isAbortOrUnload(err)) console.error("Failed to load sales conversations:", err);
    } finally {
      if (seq === requestSeqRef.current) {
        setLoading(false);
        setListLoaded(true);
      }
    }
    return undefined;
  }, []);

  useEffect(() => {
    setListLoaded(false);
    const ac = new AbortController();
    const seq = ++requestSeqRef.current;
    api.listSalesConversations(readIds(), ac.signal)
      .then((list) => {
        if (ac.signal.aborted || seq !== requestSeqRef.current) return;
        setConversations(list);
        writeIds(list.map((c) => c.id));
      })
      .catch((err) => {
        if (!isAbortOrUnload(err)) console.error("Failed to load sales conversations:", err);
      })
      .finally(() => {
        if (!ac.signal.aborted && seq === requestSeqRef.current) setListLoaded(true);
      });
    return () => ac.abort();
  }, []);

  const create = useCallback(async () => {
    const id = await api.createSalesConversation();
    rememberId(id);
    const now = Math.floor(Date.now() / 1000);
    setConversations((prev) => [
      {
        id,
        title: "Sales conversation",
        created_at: now,
        updated_at: now,
        message_count: 0,
      },
      ...prev.filter((c) => c.id !== id),
    ]);
    setListLoaded(true);
    setLoading(false);
    void refresh();
    return id;
  }, [refresh]);

  const addExisting = useCallback((id: string) => {
    rememberId(id);
  }, []);

  const remove = useCallback(
    async (convId: string): Promise<Conversation[] | undefined> => {
      try {
        await api.deleteSalesConversation(convId);
      } finally {
        forgetIds([convId]);
      }
      return (await refresh()) ?? undefined;
    },
    [refresh],
  );

  const removeMany = useCallback(
    async (convIds: string[]): Promise<Conversation[] | undefined> => {
      if (convIds.length === 0) return undefined;
      for (const id of convIds) {
        try {
          await api.deleteSalesConversation(id);
        } finally {
          forgetIds([id]);
        }
      }
      return (await refresh()) ?? undefined;
    },
    [refresh],
  );

  const rename = useCallback(
    async (convId: string, title: string) => {
      await api.updateSalesConversationTitle(convId, title);
      await refresh();
    },
    [refresh],
  );

  return {
    conversations,
    loading,
    listLoaded,
    refresh,
    create,
    addExisting,
    remove,
    removeMany,
    rename,
  };
}
