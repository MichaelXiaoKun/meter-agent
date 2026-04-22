import { useLayoutEffect, useMemo, useState, type RefObject } from "react";
import { itemHeight, type ListItem } from "../conversationListModel";

/**
 * Windowed slice for long flat lists: only indices ``[start, end]`` mount;
 * fixed row/header heights from ``itemHeight``.
 */
export function useListVirtualWindow(
  items: ListItem[],
  parentRef: RefObject<HTMLDivElement | null>,
  overscan = 6
): { start: number; end: number; totalSize: number; getOffset: (i: number) => number; rowHeights: number[] } {
  const { offsets, totalSize, rowHeights } = useMemo(() => {
    const h: number[] = [];
    const o: number[] = [];
    let y = 0;
    for (let i = 0; i < items.length; i++) {
      o.push(y);
      const z = itemHeight(items[i]!);
      h.push(z);
      y += z;
    }
    return { offsets: o, totalSize: y, rowHeights: h };
  }, [items]);

  const [win, setWin] = useState({ start: 0, end: 0 });

  useLayoutEffect(() => {
    const el = parentRef.current;
    if (!el || items.length === 0) {
      setWin({ start: 0, end: 0 });
      return;
    }
    const update = () => {
      const st = el.scrollTop;
      const bottom = st + el.clientHeight;
      let first = 0;
      for (let i = 0; i < items.length; i++) {
        if (offsets[i]! + rowHeights[i]! > st) {
          first = i;
          break;
        }
      }
      let last = items.length - 1;
      for (let i = first; i < items.length; i++) {
        if (offsets[i]! > bottom) {
          last = i - 1;
          break;
        }
      }
      if (last < first) last = first;
      const start = Math.max(0, first - overscan);
      const end = Math.min(items.length - 1, last + overscan);
      setWin((prev) => {
        if (prev.start === start && prev.end === end) return prev;
        return { start, end };
      });
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [items, offsets, rowHeights, parentRef, overscan, items.length]);

  const getOffset = (i: number) => offsets[i] ?? 0;
  return { start: win.start, end: win.end, totalSize, getOffset, rowHeights };
}
