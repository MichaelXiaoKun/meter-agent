import { useCallback, useEffect, useState } from "react";
import type { Conversation } from "../types";

const STORAGE_KEY = "bb_conv_sidebar_v1";

type FileShape = {
  pins: Record<string, string[]>;
  read: Record<string, Record<string, number>>;
};

function readFile(): FileShape {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { pins: {}, read: {} };
    const p = JSON.parse(raw) as FileShape;
    return {
      pins: typeof p.pins === "object" && p.pins ? p.pins : {},
      read: typeof p.read === "object" && p.read ? p.read : {},
    };
  } catch {
    return { pins: {}, read: {} };
  }
}

function writeFile(f: FileShape) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(f));
  } catch {
    /* ignore */
  }
}

export function useConversationListPrefs(user: string, conversations: Conversation[]) {
  const [pins, setPins] = useState<string[]>([]);
  const [readMap, setReadMap] = useState<Record<string, number>>({});

  useEffect(() => {
    if (!user) {
      setPins([]);
      setReadMap({});
      return;
    }
    const f = readFile();
    setPins(f.pins[user] ?? []);
    setReadMap(f.read[user] ?? {});
  }, [user]);

  useEffect(() => {
    if (!user || conversations.length === 0) return;
    const ids = new Set(conversations.map((c) => c.id));
    setPins((prev) => {
      const next = prev.filter((id) => ids.has(id));
      if (next.length === prev.length) return prev;
      const f = readFile();
      f.pins[user] = next;
      writeFile(f);
      return next;
    });
    setReadMap((prev) => {
      const next: Record<string, number> = {};
      for (const k of Object.keys(prev)) {
        if (ids.has(k)) next[k] = prev[k]!;
      }
      if (Object.keys(next).length === Object.keys(prev).length) return prev;
      const f = readFile();
      f.read[user] = next;
      writeFile(f);
      return next;
    });
  }, [user, conversations]);

  const persistPins = useCallback(
    (next: string[]) => {
      if (!user) return;
      const f = readFile();
      f.pins[user] = next;
      writeFile(f);
    },
    [user]
  );

  const persistRead = useCallback(
    (next: Record<string, number>) => {
      if (!user) return;
      const f = readFile();
      f.read[user] = next;
      writeFile(f);
    },
    [user]
  );

  const togglePin = useCallback(
    (convId: string) => {
      setPins((prev) => {
        const next = prev.includes(convId)
          ? prev.filter((id) => id !== convId)
          : [...prev, convId];
        persistPins(next);
        return next;
      });
    },
    [persistPins]
  );

  const markRead = useCallback(
    (convId: string, serverUpdatedAt: number) => {
      setReadMap((prev) => {
        const next = { ...prev, [convId]: serverUpdatedAt };
        persistRead(next);
        return next;
      });
    },
    [persistRead]
  );

  return { pins, readMap, togglePin, markRead };
}
