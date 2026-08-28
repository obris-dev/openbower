"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Button,
  Dropdown,
  DropdownButton,
  DropdownItem,
  DropdownMenu,
  Input,
  useToast,
} from "@bower/ui";
import { ChevronDown, MoreHorizontal, Pencil } from "lucide-react";
import {
  deleteList,
  fetchAgentCatalog,
  fetchList,
  fetchListRows,
  GENERIC_FAILURE,
  ROWS_PAGE_LIMIT,
  loginUrl,
  postAiColumn,
  postColumn,
  postFillRefill,
  reorderColumns,
  renameColumn,
  deleteColumn,
  type ListColumn,
  type ColumnType,
  updateList,
  webRoutes,
  type FillWire,
  type RenderableListRowsPage,
  type RenderableListRow,
  type ListSummary,
  COLUMN_ORDER_STALE_CODE,
  ROW_COUNT_CHANGED_CODE,
} from "@bower/api";

import { ConfirmDelete } from "../../../_components/confirm-delete";
import { ensureOk } from "@/lib/ensure-ok";
import { AddColumnDrawer, AddColumnMenuItems, type AiColumnPayload, type BlankColumnPayload, type ColumnKind } from "./add-column";
import { FindLookalikes } from "./find-lookalikes";
import { downloadSheetCsv } from "./export";
import { FillsTray, useFill, type SearchDoor } from "./fill";
import { SheetTable } from "./sheet-table";

// How far below the viewport the scroll sentinel arms (binary): far
// enough that the next page usually lands before the user reaches the
// last loaded row.
const SCROLL_PREFETCH_MARGIN = "256px";

/** The sheet, full-bleed under the shell's chrome in three bands: one
 * slim toolbar (click-to-rename title left; the actions right, column
 * work primary per the directory doctrine, occasional verbs behind the
 * menu), the grid as the page's ONE scroll region (sticky header row,
 * an IntersectionObserver sentinel driving the keyset loadMore with
 * the button kept as fallback), and a sticky status footer (row count
 * left, the fills tray right: status speaks continuously in the status
 * bar without stealing the page, condensing to the tray's badge
 * instead of wrapping when jobs multiply or the viewport narrows). */
export function Sheet({ initialDetail, initialRows }: { initialDetail: ListSummary; initialRows: RenderableListRowsPage }) {
  const router = useRouter();
  const toast = useToast();
  const [detail, setDetail] = useState(initialDetail);
  const [rows, setRows] = useState<RenderableListRow[]>(initialRows.items);
  const [nextCursor, setNextCursor] = useState<string | null>(initialRows.next_cursor);
  const [loadingMore, setLoadingMore] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [label, setLabel] = useState(initialDetail.label);
  const [exporting, setExporting] = useState(false);
  // null = closed; the KIND arrives with the opening gesture (the
  // Add column menu), so half-open states are unrepresentable.
  const [addColumnKind, setAddColumnKind] = useState<ColumnKind | null>(null);
  // A plain column being named before it exists (see openAddColumn).
  const [pendingColumn, setPendingColumn] = useState<{ type: ColumnType } | null>(null);
  const [lookalikesOpen, setLookalikesOpen] = useState(false);

  // The fill attachment polls the FILLS alone; cell states ride the
  // rows this component already holds.
  const fill = useFill(detail.id);

  const rowsRef = useRef(rows);
  useEffect(() => {
    rowsRef.current = rows;
  }, [rows]);

  // A cell whose run had a degraded web search composes the
  // deployment's search door into its popover (the paid-door nudge
  // belongs only to the free door), fetched once and only when such a
  // cell is on screen: a sheet with none never pays for the catalog.
  // Absence degrades to the bare sentence.
  const [searchDoor, setSearchDoor] = useState<SearchDoor>(null);
  const needsSearchDoor = rows.some((row) =>
    Object.values(row.states ?? {}).some((entry) => (entry.tools.web_search ?? "open") !== "open"),
  );
  useEffect(() => {
    if (!needsSearchDoor || searchDoor !== null) return;
    let superseded = false;
    async function load() {
      const res = await fetchAgentCatalog();
      if (!superseded && res.status === "ok") setSearchDoor(res.data.search_provider);
    }
    void load();
    return () => {
      superseded = true;
    };
  }, [needsSearchDoor, searchDoor]);

  // Reorder is OPTIMISTIC, because a drag that waits for a round trip
  // reads as a failed drag. The server's echo replaces the guess
  // either way: on success it is the same order, and on refusal (a
  // teammate added or removed a column since this sheet was read) it
  // is the truth this client did not have.
  const reorderBusyRef = useRef(false);
  const reorderColumnsTo = useCallback(
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
        if (res.status === "unauthenticated") {
          window.location.href = loginUrl();
          return;
        }
        if (res.status !== "ok") {
        // The move is put back and the server's reason is spoken: a
        // reorder is detached from any form the user is looking at, so
        // it is the toast tier, not a banner.
          setDetail((current) => ({ ...current, columns: previous }));
          // Putting the old set back leaves the sheet exactly as stale
          // as the server just called it, so every later move would
          // refuse the same way until a manual reload. Re-read
          // instead, so the server's "try the move again" can succeed
          // in place.
          if (res.code === COLUMN_ORDER_STALE_CODE) {
            const summary = await fetchList(detail.id);
            if (summary.status === "unauthenticated") {
              window.location.href = loginUrl();
              return;
            }
            if (summary.status === "ok") {
              setDetail(summary.data);
              setLabel(summary.data.label);
            }
          }
          toast.error(res.message, "Columns not reordered");
          return;
        }
        setDetail(res.data);
        setLabel(res.data.label);
      } finally {
        reorderBusyRef.current = false;
      }
    },
    [detail, toast],
  );

  const renameColumnTo = useCallback(
    async (key: string, label: string) => {
      const previous = detail.columns;
      // Optimistic like the reorder: a rename is direct manipulation,
      // so the header has to change under the pointer.
      setDetail((current) => ({
        ...current,
        columns: current.columns.map((column) => (column.key === key ? { ...column, label } : column)),
      }));
      const res = await renameColumn(detail.id, key, label);
      if (res.status === "unauthenticated") {
        window.location.href = loginUrl();
        return;
      }
      if (res.status !== "ok") {
        setDetail((current) => ({ ...current, columns: previous }));
        toast.error(res.message, "Column not renamed");
        return;
      }
      setDetail(res.data);
      setLabel(res.data.label);
    },
    [detail, toast],
  );

  const refreshBusyRef = useRef(false);
  // Re-reads the pages already on screen. It is the ONLY walk of the
  // rows now, and it carries their states with them, so a value and
  // its state can never come from different requests.
  // Positions are append-only, so a wholesale replacement keeps the
  // paging coherent. Silent on blips: the poll loop owns trouble
  // surfacing, and a toast every interval would be noise.
  const refreshLoadedRows = useCallback(async () => {
    if (refreshBusyRef.current) return;
    refreshBusyRef.current = true;
    try {
      const target = Math.max(rowsRef.current.length, 1);
      const items: RenderableListRow[] = [];
      let after: string | undefined;
      let cursor: string | null = null;
      for (;;) {
        const res = await fetchListRows(detail.id, { after, limit: ROWS_PAGE_LIMIT });
        if (res.status === "unauthenticated") {
          window.location.href = loginUrl();
          return;
        }
        if (res.status !== "ok") return;
        items.push(...res.data.items);
        cursor = res.data.next_cursor;
        if (!cursor || items.length >= target) break;
        after = cursor;
      }
      setRows(items);
      setNextCursor(cursor);
    } finally {
      refreshBusyRef.current = false;
    }
  }, [detail.id]);

  // Confirmed in the menu panel that asked, so this just does it.
  const removeColumn = useCallback(
    async (column: ListColumn) => {
      const res = await deleteColumn(detail.id, column.key);
      if (res.status === "unauthenticated") {
        window.location.href = loginUrl();
        return;
      }
      if (res.status !== "ok") {
        toast.error(res.message, "Column not deleted");
        return;
      }
      setDetail(res.data);
      setLabel(res.data.label);
      // The values left with the column, so the loaded rows still
      // carry a key the sheet no longer has a header for.
      await refreshLoadedRows();
    },
    [detail.id, toast, refreshLoadedRows],
  );

  // Rows re-read when a live job progressed (status or attempted
  // moved), on first sight of a live job, and once on the
  // last-live-to-terminal edge (cells written between polls land in
  // that final read). Jobs already terminal on mount trigger nothing:
  // the server rendered their rows fresh.
  const jobsSignature = fill.jobs.map((job) => `${job.id}:${job.status}:${job.counters.attempted}`).join(" ");
  const anyLive = fill.jobs.some((job) => job.status === "pending" || job.status === "running");
  const prevJobsRef = useRef<{ signature: string; live: boolean } | null>(null);
  useEffect(() => {
    if (!jobsSignature) return;
    const prev = prevJobsRef.current;
    prevJobsRef.current = { signature: jobsSignature, live: anyLive };
    const progressed = prev === null || prev.signature !== jobsSignature;
    if ((anyLive && progressed) || (prev !== null && prev.live && !anyLive)) void refreshLoadedRows();
  }, [jobsSignature, anyLive, refreshLoadedRows]);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    const res = await fetchListRows(detail.id, { after: nextCursor, limit: ROWS_PAGE_LIMIT });
    setLoadingMore(false);
    if (!ensureOk(res, toast)) return;
    setRows((prev) => [...prev, ...res.data.items]);
    setNextCursor(res.data.next_cursor);
  }, [detail.id, nextCursor, loadingMore, toast]);

  // Infinite scroll: a sentinel inside the grid's own scroll region
  // drives loadMore as it comes into view (the button below stays as
  // the fallback for environments without IntersectionObserver
  // semantics, and loadingMore guards double-fires). The observer is
  // rebuilt when loadMore's inputs move; a still-visible sentinel then
  // fires again, which IS the continuous walk.
  // The sheet OWNS the viewport (the grid band is the only
  // scroller), so body scroll locks while this route is mounted: the
  // full-screen-surface pattern, scoped here rather than globally
  // (ordinary pages keep the body's y-scroll). Restores on unmount.
  useEffect(() => {
    const previous = document.body.style.overflowY;
    document.body.style.overflowY = "hidden";
    return () => {
      document.body.style.overflowY = previous;
    };
  }, []);

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

  // The drawer's invoker (the toolbar primary or the "+" header cell)
  // gets focus back on close: ref focus for an imperative gesture
  // outside a mount, per the house focus rules.
  const addColumnInvokerRef = useRef<HTMLElement | null>(null);
  // A PLAIN column is a name and a type, which is not a drawer's worth
  // of decisions: it opens a pending header cell and is named in the
  // grid. Only the AI kind keeps the drawer, where a prompt, a model,
  // outputs and tools have to be chosen.
  function openAddColumn(kind: ColumnKind) {
    if (kind !== "ai") {
      setPendingColumn({ type: kind });
      return;
    }
    addColumnInvokerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setAddColumnKind(kind);
  }

  async function namePendingColumn(label: string) {
    const type = pendingColumn?.type;
    if (type === undefined) return;
    // Abandoned (Escape, or nothing typed): nothing was created, so
    // there is nothing to undo.
    if (!label) {
      setPendingColumn(null);
      return;
    }
    const res = await postColumn(detail.id, { label, type });
    if (res.status === "unauthenticated") {
      window.location.href = loginUrl();
      return;
    }
    if (res.status !== "ok") {
      // The cell STAYS open on a refusal (a taken name, a reserved
      // key, the cap): the request is what has to change, and closing
      // it would throw away what they typed.
      toast.error(res.message, "Column not added");
      return;
    }
    setPendingColumn(null);
    setDetail(res.data);
    setLabel(res.data.label);
  }
  function closeAddColumn() {
    setAddColumnKind(null);
    addColumnInvokerRef.current?.focus();
    addColumnInvokerRef.current = null;
  }

  async function submitRename() {
    const next = label.trim();
    setRenaming(false);
    if (!next || next === detail.label) {
      setLabel(detail.label);
      return;
    }
    const res = await updateList(detail.id, { label: next });
    if (!ensureOk(res, toast)) {
      setLabel(detail.label);
      return;
    }
    setDetail(res.data);
    setLabel(res.data.label);
    router.refresh();
  }

  async function remove() {
    const res = await deleteList(detail.id);
    if (!ensureOk(res, toast)) return;
    toast.success("List deleted.", detail.label);
    router.push(webRoutes.lists);
    router.refresh();
  }

  async function exportCsv() {
    setExporting(true);
    try {
      await downloadSheetCsv(detail, { onUnauthenticated: () => (window.location.href = loginUrl()) });
      toast.success("CSV downloaded.", detail.label);
    } catch {
      toast.error("Download failed part-way. Try again.");
    } finally {
      setExporting(false);
    }
  }

  // The drawer renders refusals itself (field-level where it can), so
  // the error envelope maps through instead of toasting here: `error`
  // is the machine code, `detail` the server's verbatim copy.
  async function submitAiColumn(payload: AiColumnPayload): Promise<{ ok: true } | { ok: false; error: string; detail: string }> {
    const res = await postAiColumn(detail.id, payload);
    if (res.status === "unauthenticated") {
      window.location.href = loginUrl();
      return { ok: false, error: "", detail: "" };
    }
    if (res.status !== "ok") {
      // A row-count echo refusal means the drawer's numbers are stale:
      // re-read the summary so the rendered count and the next
      // attempt's confirmed_row_count echo the sheet's new truth (the
      // server's "start again" must be able to succeed in place).
      if (res.code === ROW_COUNT_CHANGED_CODE) {
        const summary = await fetchList(detail.id);
        if (summary.status === "ok") {
          setDetail(summary.data);
          setLabel(summary.data.label);
        }
      }
      return { ok: false, error: res.code ?? "", detail: res.message };
    }
    closeAddColumn();
    // The new column (and any test-run prewrite cells) exist only
    // server-side: re-read the summary for the column set, re-attach
    // to the fresh job, and re-read the loaded rows.
    const summary = await fetchList(detail.id);
    if (summary.status === "ok") {
      setDetail(summary.data);
      setLabel(summary.data.label);
    }
    // The poll loop's promise settles only when the fill ENDS, so the
    // attach is fire-and-forget (it happens on the loop's first tick);
    // only the bounded row re-read is awaited.
    void fill.refresh();
    await refreshLoadedRows();
    router.refresh();
    return { ok: true };
  }

  // Continue IS refill: a NEW job over the column's unanswered rows
  // (all of them, or the next `rows` when the tracker's scoped
  // continue asked). The COLUMN comes from the surface the user
  // clicked: one job can map several columns, so deriving it from the
  // job would refill a sibling. A refusal returns as the server's
  // verbatim detail for the chip's error slot.
  async function continueFill(
    job: FillWire | null,
    columnKey: string,
    opts: { rows?: number; resume?: boolean } = {},
  ): Promise<string | null> {
    // A widening refill needs no job envelope: only RESUME is bound to
    // one, and a column whose job has aged off the fetched page can
    // still be filled forward.
    if (!columnKey || (opts.resume && job === null)) return GENERIC_FAILURE;
    const res = await postFillRefill(detail.id, columnKey, { rows: opts.rows, resumeFill: opts.resume && job ? job.id : undefined });
    if (res.status === "unauthenticated") {
      window.location.href = loginUrl();
      return null;
    }
    if (res.status !== "ok") return res.message;
    // The new job and its pending outcomes exist only server-side:
    // re-attach the poll loop (fire-and-forget; its promise settles
    // when the fill ENDS) and re-read the loaded rows.
    void fill.refresh();
    await refreshLoadedRows();
    return null;
  }

  // The blank add starts nothing: the 200 body IS the updated summary,
  // so the new column renders straight from the response.
  async function submitBlankColumn(payload: BlankColumnPayload): Promise<{ ok: true } | { ok: false; error: string; detail: string }> {
    const res = await postColumn(detail.id, payload);
    if (res.status === "unauthenticated") {
      window.location.href = loginUrl();
      return { ok: false, error: "", detail: "" };
    }
    if (res.status !== "ok") return { ok: false, error: res.code ?? "", detail: res.message };
    closeAddColumn();
    setDetail(res.data);
    setLabel(res.data.label);
    toast.success(`Added the ${payload.label} column.`);
    router.refresh();
    return { ok: true };
  }

  return (
    // FILLS the page's pinned wrapper ([id]/page.tsx owns the
    // viewport calc and clips): three bands split the height; only
    // the grid band scrolls, the page itself never does.
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-hairline px-4 py-2">
        <div className="min-w-0">
          {renaming ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submitRename();
              }}
            >
              <Input
                autoFocus
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                onBlur={() => void submitRename()}
                onKeyDown={(e) => {
                  // Escape cancels: unmounting fires no blur, so nothing
                  // commits.
                  if (e.key === "Escape") {
                    setLabel(detail.label);
                    setRenaming(false);
                  }
                }}
                className="max-w-xs py-1 text-sm font-semibold"
                aria-label="List name"
              />
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setRenaming(true)}
              title="Rename"
              className="group flex min-w-0 items-center gap-1.5 rounded text-left text-sm font-semibold text-foreground"
            >
              <span className="truncate group-hover:text-signal">{detail.label}</span>
              {/* Always visible (touch has no hover): the one hint the
                  title is editable. */}
              <Pencil aria-hidden className="h-3.5 w-3.5 shrink-0 text-faint group-hover:text-signal" />
            </button>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {confirmingDelete ? (
            // Destruction confirms IN PLACE (the same slot the verb came
            // from), naming what dies; the ONE shared ritual, no modal.
            <ConfirmDelete
              inline
              question="Delete this list?"
              onCancel={() => setConfirmingDelete(false)}
              onDelete={() => void remove()}
            />
          ) : (
            <>
              {/* Column work holds the primary slot now that fills
                  exist; the discover seed lives in the menu and its
                  disclosure opens here when summoned. */}
              <FindLookalikes detail={detail} open={lookalikesOpen} onOpenChange={setLookalikesOpen} />
              {/* A MENU button: the kind is chosen at
                  the gesture, so the drawer opens already knowing
                  what it is; new column kinds land here later. */}
              <Dropdown>
                <DropdownButton as={Button} size="sm">
                  Add column
                  <ChevronDown aria-hidden className="ml-1.5 h-4 w-4" />
                </DropdownButton>
                <DropdownMenu anchor="bottom end">
                  <AddColumnMenuItems onPick={openAddColumn} />
                </DropdownMenu>
              </Dropdown>
              <Dropdown>
                <DropdownButton
                  aria-label="List actions"
                  className="rounded-md p-2 text-faint hover:bg-wash hover:text-foreground"
                >
                  <MoreHorizontal aria-hidden className="h-5 w-5" />
                </DropdownButton>
                <DropdownMenu anchor="bottom end">
                  {/* Workflow verbs above management verbs. */}
                  <DropdownItem
                    disabled={detail.columns.length === 0 || detail.row_count === 0}
                    onClick={() => setLookalikesOpen(true)}
                  >
                    Find lookalikes
                  </DropdownItem>
                  <DropdownItem disabled={exporting || detail.row_count === 0} onClick={() => void exportCsv()}>
                    {exporting ? "Exporting…" : "Export CSV"}
                  </DropdownItem>
                  <DropdownItem className="text-danger" onClick={() => setConfirmingDelete(true)}>
                    Delete list
                  </DropdownItem>
                </DropdownMenu>
              </Dropdown>
            </>
          )}
        </div>
      </div>

      <AddColumnDrawer
        open={addColumnKind !== null}
        kind={addColumnKind ?? "ai"}
        onClose={closeAddColumn}
        rowCount={detail.row_count}
        columns={detail.columns}
        onSubmit={submitAiColumn}
        onAddBlank={submitBlankColumn}
      />

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto">
        <SheetTable
          columns={detail.columns}
          rows={rows}
          searchDoor={searchDoor}
          fills={{ listId: detail.id, jobs: fill.jobs, summaries: fill.summaries, rowCount: detail.row_count, onStop: fill.stop, onRefill: continueFill }}
          onAddColumn={openAddColumn}
          onReorder={reorderColumnsTo}
          onRenameColumn={renameColumnTo}
          onDeleteColumn={removeColumn}
          pendingColumn={pendingColumn}
          onNamePending={(label) => void namePendingColumn(label)}
        />
        {nextCursor && (
          <>
            <div ref={sentinelRef} aria-hidden />
            <div className="border-t border-hairline p-3 text-center">
              <Button size="sm" variant="ghost" loading={loadingMore} onClick={() => void loadMore()}>
                Load more ({rows.length.toLocaleString("en-US")} of {detail.row_count.toLocaleString("en-US")})
              </Button>
            </div>
          </>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3 border-t border-hairline px-4 py-1.5">
        {/* The count abbreviates under width pressure (the word drops
            below sm); the fills area condenses instead, inside the
            tray, so the band never wraps chips onto a second line. */}
        <p className="shrink-0 whitespace-nowrap text-xs text-muted">
          {detail.row_count.toLocaleString("en-US")}
          <span className="hidden sm:inline">{" rows"}</span>
        </p>
        <div className="flex min-w-0 flex-col items-end gap-1">
          <FillsTray jobs={fill.jobs} onStop={fill.stop} onContinue={continueFill} />
          {fill.pollTrouble && (
            // Client-only fact, phrased as one: the page cannot see the
            // server, so it claims nothing about the fill itself. It is
            // the PAGE's trouble, so it renders once (under the band,
            // never inside the tray's panel, which may be closed).
            <p className="text-xs text-warning">{"Progress updates aren't reaching this page; still retrying."}</p>
          )}
        </div>
      </div>
    </div>
  );
}
