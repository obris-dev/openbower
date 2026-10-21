"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { useToast } from "@bower/ui";
import {
  GENERIC_FAILURE,
  ROWS_PAGE_LIMIT,
  addListRows,
  deleteListRow,
  deleteListRows,
  fetchListRows,
  setListRowCells,
  type RenderableListRow,
  type RenderableListRowsPage,
  type RowSelectionBody,
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
  setCells: (rowId: string, data: Record<string, string>) => Promise<RowOutcome>;
  removeRow: (rowId: string) => Promise<RowOutcome>;
  removeRows: (body: RowSelectionBody) => Promise<RowsOutcome>;
  addRows: (count: number) => Promise<AddRowsOutcome>;
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
  // A person's write moves the generation. A re-read that went out
  // before a write came back carries rows older than the ones the
  // write swapped in, so a result from an earlier generation is
  // dropped rather than put over them; the next re-read carries the
  // write.
  const refreshBusyRef = useRef(false);
  const writeGenerationRef = useRef(0);
  const refreshLoaded = useCallback(async (): Promise<boolean> => {
    if (refreshBusyRef.current) return true;
    refreshBusyRef.current = true;
    try {
      const generation = writeGenerationRef.current;
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
      if (generation !== writeGenerationRef.current) return true;
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

  // A person's write on one row: the server answers with the row as it
  // now reads, states included, so it is swapped in whole and nothing
  // is re-read. A refusal comes back as the server's sentence for the
  // caller to show; a transport failure as the generic one.
  const setCells = useCallback(
    async (rowId: string, data: Record<string, string>): Promise<RowOutcome> => {
      const res = await setListRowCells(listId, rowId, data);
      if (redirectIfUnauthenticated(res)) return LEAVING;
      if (res.status !== "ok") return { ok: false, detail: res.message, refused: res.code !== undefined };
      writeGenerationRef.current += 1;
      setRows((prev) => prev.map((row) => (row.id === rowId ? res.data : row)));
      return { ok: true };
    },
    [listId],
  );

  // A row's removal: gone from the page the moment the server agrees.
  // The count in the footer is the detail's, which the caller refreshes.
  const removeRow = useCallback(
    async (rowId: string): Promise<RowOutcome> => {
      const res = await deleteListRow(listId, rowId);
      if (redirectIfUnauthenticated(res)) return LEAVING;
      if (res.status !== "ok") return { ok: false, detail: res.message, refused: res.code !== undefined };
      writeGenerationRef.current += 1;
      setRows((prev) => prev.filter((row) => row.id !== rowId));
      return { ok: true };
    },
    [listId],
  );

  // A selection's removal. Named rows drop out; under "all", only the
  // rows kept remain, and every one of those is loaded (only a loaded
  // row can be unticked), so the page is exact and the cursor is
  // spent: nothing is left beyond it.
  const removeRows = useCallback(
    async (body: RowSelectionBody): Promise<RowsOutcome> => {
      const res = await deleteListRows(listId, body);
      if (redirectIfUnauthenticated(res)) return LEAVING_ROWS;
      if (res.status !== "ok") return { ok: false, detail: res.message, refused: res.code !== undefined };
      writeGenerationRef.current += 1;
      if ("row_ids" in body) {
        const gone = new Set(body.row_ids);
        setRows((prev) => prev.filter((row) => !gone.has(row.id)));
      } else {
        const kept = new Set(body.keep);
        setRows((prev) => prev.filter((row) => kept.has(row.id)));
        setNextCursor(null);
      }
      return { ok: true, deleted: res.data.deleted, rowCount: res.data.row_count };
    },
    [listId],
  );

  // Blank rows from the add-rows control: the receipt carries them in
  // the page's shape, so they join the end of the loaded rows (the
  // append ranks them last, and the control shows only once the end
  // is loaded) and nothing is re-read.
  const addRows = useCallback(
    async (count: number): Promise<AddRowsOutcome> => {
      const res = await addListRows(listId, Array.from({ length: count }, () => ({})));
      if (redirectIfUnauthenticated(res)) return LEAVING_ADD;
      if (res.status !== "ok") return { ok: false, detail: res.message, refused: res.code !== undefined };
      const made = res.data.rows;
      const first = made[0];
      if (!first) return { ok: false, detail: GENERIC_FAILURE, refused: false };
      writeGenerationRef.current += 1;
      setRows((prev) => [...prev, ...made]);
      return { ok: true, firstRowId: first.id, added: made.length };
    },
    [listId],
  );

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
  return {
    rows,
    hasMore: Boolean(nextCursor),
    loadingMore,
    loadMore,
    refreshLoaded,
    setCells,
    removeRow,
    removeRows,
    addRows,
    scrollRef,
    sentinelRef,
  };
}

/** What a row write or removal came to: done, or not, with a sentence
 * the caller's toast renders verbatim. `refused` tells the server's
 * answer (a classified refusal, in its words: the value was judged)
 * from a failure that judged nothing (the network, a 500, a row gone
 * meanwhile), so the toast's title claims a refusal only when one
 * happened. An empty sentence is the page leaving for login: not done,
 * and nothing to say while navigation lands. */
export type RowOutcome = { ok: true } | { ok: false; detail: string; refused: boolean };

const LEAVING: RowOutcome = { ok: false, detail: "", refused: false };

/** A rows delete's outcome: the row outcome's shape with the receipt
 * (how many went, the sheet's count after) on success. */
export type RowsOutcome = { ok: true; deleted: number; rowCount: number } | Extract<RowOutcome, { ok: false }>;

const LEAVING_ROWS: RowsOutcome = { ok: false, detail: "", refused: false };

/** An add's outcome: how many rows were made and the first one's id,
 * so focus can land on it. */
export type AddRowsOutcome =
  | { ok: true; firstRowId: string; added: number }
  | Extract<RowOutcome, { ok: false }>;

const LEAVING_ADD: AddRowsOutcome = { ok: false, detail: "", refused: false };
