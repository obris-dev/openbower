"use client";

import { useCallback, useRef, useState } from "react";
import { useToast } from "@bower/ui";
import {
  COLUMN_ORDER_STALE_CODE,
  COLUMN_WAITED_ON_CODE,
  GENERIC_FAILURE,
  deleteColumn,
  fetchList,
  postAiColumn,
  postColumn,
  postColumnWebhook,
  renameColumn,
  reorderColumns,
  updateColumnWebhook,
  updateList,
  type AiColumnBody,
  type ColumnType,
  type ListColumn,
  type ListDetail,
  type WebhookColumnBody,
  type WebhookColumnPatchBody,
} from "@bower/api";

import { ensureOk, redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { columnsInKeyOrder } from "./lib/column-order";
import { createdColumnKey } from "./lib/created-columns";
import type { ColumnOutcome } from "./use-ai";

// Re-exported beside the ops that produce it, so the sheet imports
// its hook surface from one place; the TYPE lives with the drawer
// that renders it.
export type { ColumnOutcome };

/** The AI column create's outcome: on success, one key it added, so
 * the caller can fill the agent's columns (they fill as one unit). */
export type AiColumnOutcome = { ok: true; key: string } | Extract<ColumnOutcome, { ok: false }>;

// The outcome when the page is leaving for login: no code and no
// copy, so no surface renders a refusal while navigation lands.
const LEAVING: Extract<ColumnOutcome, { ok: false }> = { ok: false, error: "", detail: "" };

/** The list detail and every write to it. Column writes echo the
 * WHOLE detail back (columns, row count, label, entry actions), so one owner holds
 * it and every op replaces it from the response; the title rename is
 * the one non-column write and rides here for the same reason. What
 * a write means for the rest of the sheet (rows to re-read, a fill to
 * re-attach, a drawer to close) is the sheet's composition, not this
 * hook's: each op reports its outcome and stops. */
export function useColumns(initialDetail: ListDetail): {
  detail: ListDetail;
  pendingColumn: { type: ColumnType } | null;
  reorder: (keys: string[]) => Promise<void>;
  rename: (key: string, label: string) => Promise<void>;
  remove: (column: ListColumn) => Promise<ColumnOutcome>;
  addAi: (body: AiColumnBody) => Promise<AiColumnOutcome>;
  addWebhook: (body: WebhookColumnBody) => Promise<ColumnOutcome>;
  saveWebhook: (key: string, body: WebhookColumnPatchBody) => Promise<ColumnOutcome>;
  startPending: (type: ColumnType) => void;
  namePending: (label: string) => Promise<void>;
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
  // SILENT on a non-ok by design: this runs only on refusal paths,
  // where the caller's own toast or rendered refusal is the voice and
  // a second message would be noise.
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
      const moved = columnsInKeyOrder(previous, keys);
      if (moved === null) return;
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

  // Confirmed in the menu panel that asked, so this just does it. Ok
  // when the column is gone (its values went with it, which the caller
  // reflects in the rows it holds). A refusal in use (a webhook column
  // waits on it) comes BACK as the outcome so the tier that asked
  // renders the server's sentence in place; any other failure toasts
  // here and returns an empty outcome, so the tier folds.
  const remove = useCallback(
    async (column: ListColumn): Promise<ColumnOutcome> => {
      const res = await deleteColumn(detail.id, column.key);
      if (redirectIfUnauthenticated(res)) return LEAVING;
      if (res.status !== "ok") {
        if (res.code === COLUMN_WAITED_ON_CODE) return { ok: false, error: res.code, detail: res.message };
        toast.error(res.message, "Column not deleted");
        return LEAVING;
      }
      setDetail(res.data);
      return { ok: true };
    },
    [detail.id, toast],
  );

  // The webhook add echoes the summary (the column is a structural
  // write, nothing starts), so the new column renders straight from
  // the response; a save rewrites the nodes and echoes nothing the
  // summary shows.
  const addWebhook = useCallback(
    async (body: WebhookColumnBody): Promise<ColumnOutcome> => {
      const res = await postColumnWebhook(detail.id, body);
      if (redirectIfUnauthenticated(res)) return LEAVING;
      if (res.status !== "ok") return { ok: false, error: res.code ?? "", detail: res.message };
      setDetail(res.data);
      return { ok: true };
    },
    [detail.id],
  );

  const saveWebhook = useCallback(
    async (key: string, body: WebhookColumnPatchBody): Promise<ColumnOutcome> => {
      const res = await updateColumnWebhook(detail.id, key, body);
      if (redirectIfUnauthenticated(res)) return LEAVING;
      if (res.status !== "ok") return { ok: false, error: res.code ?? "", detail: res.message };
      return { ok: true };
    },
    [detail.id],
  );

  // The blank add starts nothing: the 200 body IS the updated detail,
  // so the new column renders straight from the response.
  // The AI add starts nothing either: it creates the column set and
  // echoes the detail. The new column is read from that reply alone
  // (createdColumnKey), handed back so the caller can fill it. A reply
  // ending in no AI column is a contract break, not a state to render.
  const addAi = useCallback(
    async (body: AiColumnBody): Promise<AiColumnOutcome> => {
      const res = await postAiColumn(detail.id, body);
      if (redirectIfUnauthenticated(res)) return LEAVING;
      if (res.status !== "ok") return { ok: false, error: res.code ?? "", detail: res.message };
      setDetail(res.data);
      const key = createdColumnKey(res.data.columns);
      if (key === null) return { ok: false, error: "", detail: GENERIC_FAILURE };
      return { ok: true, key };
    },
    [detail.id],
  );

  // A PLAIN column is a name and a type, which is not a drawer's worth
  // of decisions: it opens a pending header cell and is named in the
  // grid.
  const startPending = useCallback((type: ColumnType) => setPendingColumn({ type }), []);

  // On a refusal (a taken name, a reserved key, the cap) the cell
  // STAYS open, because the request is what has to change and closing
  // it would throw away what they typed; `pendingColumn` carries that
  // fact, so there is no return value to read.
  const namePending = useCallback(
    async (label: string): Promise<void> => {
      const type = pendingColumn?.type;
      if (type === undefined) return;
      // Abandoned (Escape, or nothing typed): nothing was created, so
      // there is nothing to undo.
      if (!label) {
        setPendingColumn(null);
        return;
      }
      const res = await postColumn(detail.id, { label, type });
      if (!ensureOk(res, toast, { title: "Column not added" })) return;
      setPendingColumn(null);
      setDetail(res.data);
    },
    [detail.id, pendingColumn, toast],
  );

  const renameList = useCallback(
    async (label: string): Promise<boolean> => {
      const res = await updateList(detail.id, { label });
      if (!ensureOk(res, toast, { title: "List not renamed" })) return false;
      setDetail(res.data);
      return true;
    },
    [detail.id, toast],
  );

  return {
    detail,
    pendingColumn,
    reorder,
    rename,
    remove,
    addAi,
    addWebhook,
    saveWebhook,
    startPending,
    namePending,
    renameList,
  };
}
