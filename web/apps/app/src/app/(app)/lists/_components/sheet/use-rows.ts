"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { useToast } from "@bower/ui";
import {
  ROWS_PAGE_LIMIT,
  fetchListRows,
  type RenderableListRow,
  type RenderableListRowsPage,
} from "@bower/api";

import { ensureOk, redirectIfUnauthenticated } from "@/lib/ensure-ok";

// How far below the viewport the scroll sentinel arms (binary): far
// enough that the next page usually lands before the user reaches the
// last loaded row.
const SCROLL_PREFETCH_MARGIN = "256px";

/** The rows on screen and the two ways they move: forward by keyset
 * page (the sentinel inside the grid's own scroll region drives it,
 * the button stays as the fallback), and a re-read of the pages
 * already loaded. The re-read is the ONLY walk of loaded rows, and it
 * carries their states with them, so a value and its state can never
 * come from different requests. `scrollRef` and `sentinelRef` are the
 * CALLER's to attach (the scroll region and the sentinel inside it);
 * the observer never arms while either is loose. */
export function useRows(
  listId: string,
  initialRows: RenderableListRowsPage,
): {
  rows: RenderableListRow[];
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => Promise<void>;
  refreshLoaded: () => Promise<boolean>;
  scrollRef: RefObject<HTMLDivElement | null>;
  sentinelRef: RefObject<HTMLDivElement | null>;
} {
  const toast = useToast();
  const [rows, setRows] = useState<RenderableListRow[]>(initialRows.items);
  const [nextCursor, setNextCursor] = useState<string | null>(initialRows.next_cursor);
  const [loadingMore, setLoadingMore] = useState(false);

  const rowsRef = useRef(rows);
  useEffect(() => {
    rowsRef.current = rows;
  }, [rows]);

  // The re-read pages from the top in sheet order to at least the
  // loaded length, so what comes back is the same prefix the gutter
  // counts and a wholesale replacement keeps the paging coherent.
  // No toast on a blip (one every interval would be noise); the
  // caller's loop counts consecutive failures and surfaces trouble.
  // Answers whether the rows were re-read: a skipped call (one already
  // in flight) counts as a read, since that one will land.
  const refreshBusyRef = useRef(false);
  const refreshLoaded = useCallback(async (): Promise<boolean> => {
    if (refreshBusyRef.current) return true;
    refreshBusyRef.current = true;
    try {
      const target = Math.max(rowsRef.current.length, 1);
      const items: RenderableListRow[] = [];
      let after: string | undefined;
      let cursor: string | null = null;
      for (;;) {
        const res = await fetchListRows(listId, { after, limit: ROWS_PAGE_LIMIT });
        if (redirectIfUnauthenticated(res)) return true;
        if (res.status !== "ok") return false;
        items.push(...res.data.items);
        cursor = res.data.next_cursor;
        if (!cursor || items.length >= target) break;
        after = cursor;
      }
      setRows(items);
      setNextCursor(cursor);
      return true;
    } finally {
      refreshBusyRef.current = false;
    }
  }, [listId]);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    const res = await fetchListRows(listId, { after: nextCursor, limit: ROWS_PAGE_LIMIT });
    setLoadingMore(false);
    if (!ensureOk(res, toast)) return;
    setRows((prev) => [...prev, ...res.data.items]);
    setNextCursor(res.data.next_cursor);
  }, [listId, nextCursor, loadingMore, toast]);

  // Infinite scroll: the sentinel drives loadMore as it comes into
  // view (loadingMore guards double-fires). The observer is rebuilt
  // when loadMore's inputs move; a still-visible sentinel then fires
  // again, which IS the continuous walk.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const root = scrollRef.current;
    const sentinel = sentinelRef.current;
    if (!root || !sentinel || !nextCursor) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) void loadMore();
      },
      { root, rootMargin: `${SCROLL_PREFETCH_MARGIN} 0px` },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [nextCursor, loadMore]);

  // Boolean(), not a null check: an empty-string cursor must read as
  // done, or the Load more button renders while loadMore's own guard
  // refuses to act on it.
  return { rows, hasMore: Boolean(nextCursor), loadingMore, loadMore, refreshLoaded, scrollRef, sentinelRef };
}
