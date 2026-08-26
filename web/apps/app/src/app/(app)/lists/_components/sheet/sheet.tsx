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
  fetchList,
  fetchListRows,
  GENERIC_FAILURE,
  ROWS_PAGE_LIMIT,
  loginUrl,
  postAiColumn,
  postColumn,
  postFillRefill,
  updateList,
  webRoutes,
  type FillWire,
  type RenderableListRowsPage,
  type RenderableListRow,
  type ListSummary,
  ROW_COUNT_CHANGED_CODE,
} from "@bower/api";

import { ConfirmDelete } from "../../../_components/confirm-delete";
import { ensureOk } from "@/lib/ensure-ok";
import { AddColumnDrawer, AddColumnMenuItems, type AiColumnPayload, type BlankColumnPayload, type ColumnKind } from "./add-column";
import { FindLookalikes } from "./find-lookalikes";
import { downloadSheetCsv } from "./export";
import { FillsTray, useFill } from "./fill";
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
  const [lookalikesOpen, setLookalikesOpen] = useState(false);

  // The fill attachment polls the FILLS alone; cell states ride the
  // rows this component already holds.
  const fill = useFill(detail.id);

  const rowsRef = useRef(rows);
  useEffect(() => {
    rowsRef.current = rows;
  }, [rows]);

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
  function openAddColumn(kind: ColumnKind) {
    addColumnInvokerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setAddColumnKind(kind);
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
          fills={{ listId: detail.id, jobs: fill.jobs, summaries: fill.summaries, rowCount: detail.row_count, onStop: fill.stop, onRefill: continueFill }}
          onAddColumn={openAddColumn}
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
