"use client";

import { useCallback, useRef, useState } from "react";
import { useToast } from "@bower/ui";
import {
  COLUMN_ORDER_STALE_CODE,
  ROW_COUNT_CHANGED_CODE,
  deleteColumn,
  fetchList,
  postAiColumn,
  postColumn,
  renameColumn,
  reorderColumns,
  updateList,
  type ColumnType,
  type ListColumn,
  type ListSummary,
} from "@bower/api";

import { ensureOk, redirectIfUnauthenticated } from "@/lib/ensure-ok";
import type { AiColumnPayload, BlankColumnPayload } from "./add-column";

/** A drawer submission's outcome: the drawer renders refusals itself
 * (field-level where it can), so the error envelope maps through
 * instead of toasting: `error` is the machine code, `detail` the
 * server's verbatim copy. */
export type ColumnOutcome = { ok: true } | { ok: false; error: string; detail: string };

const LEAVING: ColumnOutcome = { ok: false, error: "", detail: "" };

/** The list summary and every write to it. Column writes echo the
 * WHOLE summary back (columns, row count, label), so one owner holds
 * it and every op replaces it from the response; the title rename is
 * the one non-column write and rides here for the same reason. What
 * a write means for the rest of the sheet (rows to re-read, a fill to
 * re-attach, a drawer to close) is the sheet's composition, not this
 * hook's: each op reports its outcome and stops. */
export function useColumns(initialDetail: ListSummary): {
  detail: ListSummary;
  pendingColumn: { type: ColumnType } | null;
  reorder: (keys: string[]) => Promise<void>;
  rename: (key: string, label: string) => Promise<void>;
  remove: (column: ListColumn) => Promise<boolean>;
  addBlank: (payload: BlankColumnPayload) => Promise<ColumnOutcome>;
  addAi: (payload: AiColumnPayload) => Promise<ColumnOutcome>;
  startPending: (type: ColumnType) => void;
  namePending: (label: string) => Promise<boolean>;
  renameList: (label: string) => Promise<boolean>;
} {
  const toast = useToast();
  const [detail, setDetail] = useState(initialDetail);
  // A plain column being named before it exists (see startPending).
  const [pendingColumn, setPendingColumn] = useState<{ type: ColumnType } | null>(null);

  // A refusal that names stale numbers (the column order, the row
  // count) leaves the sheet exactly as stale as the server just called
  // it, so every later attempt would refuse the same way until a
  // manual reload. Re-read instead, so the server's "try again" can
  // succeed in place. True when the page is leaving for login.
  const reread = useCallback(async (): Promise<boolean> => {
    const summary = await fetchList(detail.id);
    if (redirectIfUnauthenticated(summary)) return true;
    if (summary.status === "ok") setDetail(summary.data);
    return false;
  }, [detail.id]);

  // Reorder is OPTIMISTIC, because a drag that waits for a round trip
  // reads as a failed drag. The server's echo replaces the guess
  // either way: on success it is the same order, and on refusal (a
  // teammate added or removed a column since this sheet was read) it
  // is the truth this client did not have.
  const reorderBusyRef = useRef(false);
  const reorder = useCallback(
    async (keys: string[]) => {
      if (reorderBusyRef.current) return;
      const previous = detail.columns;
      const byKey = new Map(previous.map((column) => [column.key, column]));
      const moved = keys.map((key) => byKey.get(key)).filter((column) => column !== undefined);
      if (moved.length !== previous.length) return;
      reorderBusyRef.current = true;
      // The guard spans the WHOLE exchange, recovery included: a
      // second gesture starting mid-refetch would carry its own
      // `previous` and clobber the truth this one just fetched. And
      // finally, not a trailing line, so a throw cannot pin it true
      // and kill reordering for the rest of the session.
      try {
        setDetail((current) => ({ ...current, columns: moved }));
        const res = await reorderColumns(detail.id, keys);
        if (redirectIfUnauthenticated(res)) return;
        if (res.status !== "ok") {
          // The move is put back and the server's reason is spoken: a
          // reorder is detached from any form the user is looking at,
          // so it is the toast tier, not a banner.
          setDetail((current) => ({ ...current, columns: previous }));
          if (res.code === COLUMN_ORDER_STALE_CODE && (await reread())) return;
          toast.error(res.message, "Columns not reordered");
          return;
        }
        setDetail(res.data);
      } finally {
        reorderBusyRef.current = false;
      }
    },
    [detail, toast, reread],
  );

  const rename = useCallback(
    async (key: string, label: string) => {
      const previous = detail.columns;
      // Optimistic like the reorder: a rename is direct manipulation,
      // so the header has to change under the pointer.
      setDetail((current) => ({
        ...current,
        columns: current.columns.map((column) => (column.key === key ? { ...column, label } : column)),
      }));
      const res = await renameColumn(detail.id, key, label);
      if (redirectIfUnauthenticated(res)) return;
      if (res.status !== "ok") {
        setDetail((current) => ({ ...current, columns: previous }));
        toast.error(res.message, "Column not renamed");
        return;
      }
      setDetail(res.data);
    },
    [detail, toast],
  );

  // Confirmed in the menu panel that asked, so this just does it. True
  // when the column is gone (its values went with it, which the caller
  // reflects in the rows it holds).
  const remove = useCallback(
    async (column: ListColumn): Promise<boolean> => {
      const res = await deleteColumn(detail.id, column.key);
      if (!ensureOk(res, toast, { title: "Column not deleted" })) return false;
      setDetail(res.data);
      return true;
    },
    [detail.id, toast],
  );

  // The blank add starts nothing: the 200 body IS the updated summary,
  // so the new column renders straight from the response.
  const addBlank = useCallback(
    async (payload: BlankColumnPayload): Promise<ColumnOutcome> => {
      const res = await postColumn(detail.id, payload);
      if (redirectIfUnauthenticated(res)) return LEAVING;
      if (res.status !== "ok") return { ok: false, error: res.code ?? "", detail: res.message };
      setDetail(res.data);
      return { ok: true };
    },
    [detail.id],
  );

  // The AI add starts a fill: the new column (and any test-run
  // prewrite cells) exist only server-side, so success re-reads the
  // summary for the column set. The rows and the fill attachment are
  // the caller's to refresh.
  const addAi = useCallback(
    async (payload: AiColumnPayload): Promise<ColumnOutcome> => {
      const res = await postAiColumn(detail.id, payload);
      if (redirectIfUnauthenticated(res)) return LEAVING;
      if (res.status !== "ok") {
        if (res.code === ROW_COUNT_CHANGED_CODE && (await reread())) return LEAVING;
        return { ok: false, error: res.code ?? "", detail: res.message };
      }
      if (await reread()) return LEAVING;
      return { ok: true };
    },
    [detail.id, reread],
  );

  // A PLAIN column is a name and a type, which is not a drawer's worth
  // of decisions: it opens a pending header cell and is named in the
  // grid.
  const startPending = useCallback((type: ColumnType) => setPendingColumn({ type }), []);

  // True when the pending cell is done with (created, or abandoned):
  // on a refusal (a taken name, a reserved key, the cap) the cell
  // STAYS open, because the request is what has to change and closing
  // it would throw away what they typed.
  const namePending = useCallback(
    async (label: string): Promise<boolean> => {
      const type = pendingColumn?.type;
      if (type === undefined) return true;
      // Abandoned (Escape, or nothing typed): nothing was created, so
      // there is nothing to undo.
      if (!label) {
        setPendingColumn(null);
        return true;
      }
      const res = await postColumn(detail.id, { label, type });
      if (!ensureOk(res, toast, { title: "Column not added" })) return false;
      setPendingColumn(null);
      setDetail(res.data);
      return true;
    },
    [detail.id, pendingColumn, toast],
  );

  const renameList = useCallback(
    async (label: string): Promise<boolean> => {
      const res = await updateList(detail.id, { label });
      if (!ensureOk(res, toast)) return false;
      setDetail(res.data);
      return true;
    },
    [detail.id, toast],
  );

  return { detail, pendingColumn, reorder, rename, remove, addBlank, addAi, startPending, namePending, renameList };
}
