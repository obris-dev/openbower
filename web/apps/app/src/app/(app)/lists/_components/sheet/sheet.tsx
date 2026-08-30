"use client";

import { useEffect, useRef, useState } from "react";
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
  GENERIC_FAILURE,
  loginUrl,
  postFillRefill,
  type ListColumn,
  webRoutes,
  type FillWire,
  type RenderableListRowsPage,
  type ListSummary,
} from "@bower/api";

import { ConfirmDelete } from "../../../_components/confirm-delete";
import { ensureOk, redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { AddColumnDrawer, AddColumnMenuItems, type AiColumnPayload, type BlankColumnPayload, type ColumnKind } from "./add-column";
import { FindLookalikes } from "./find-lookalikes";
import { downloadSheetCsv } from "./export";
import { FillsTray, useFill, type SearchDoor } from "./fill";
import { SheetTable } from "./sheet-table";
import { useColumns, type ColumnOutcome } from "./use-columns";
import { useRows } from "./use-rows";

/** The sheet, full-bleed under the shell's chrome in three bands: one
 * slim toolbar (click-to-rename title left; the actions right, column
 * work primary per the directory doctrine, occasional verbs behind the
 * menu), the grid as the page's ONE scroll region (sticky header row,
 * an IntersectionObserver sentinel driving the keyset loadMore with
 * the button kept as fallback), and a sticky status footer (row count
 * left, the fills tray right: status speaks continuously in the status
 * bar without stealing the page, condensing to the tray's badge
 * instead of wrapping when jobs multiply or the viewport narrows).
 * Three hooks own the three kinds of state (the summary and its
 * columns, the rows on screen, the fill attachment); this component
 * composes their reactions to each other and renders. */
export function Sheet({ initialDetail, initialRows }: { initialDetail: ListSummary; initialRows: RenderableListRowsPage }) {
  const router = useRouter();
  const toast = useToast();
  const columns = useColumns(initialDetail);
  const { detail } = columns;
  const { rows, hasMore, loadingMore, loadMore, refreshLoaded, scrollRef, sentinelRef } = useRows(detail.id, initialRows);
  // The fill attachment polls the FILLS alone; cell states ride the
  // rows this component already holds.
  const fill = useFill(detail.id);

  // The title's draft while renaming; null = not renaming, so a draft
  // without a form is unrepresentable.
  const [titleDraft, setTitleDraft] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [exporting, setExporting] = useState(false);
  // null = closed; the KIND arrives with the opening gesture (the
  // Add column menu), so half-open states are unrepresentable.
  const [addColumnKind, setAddColumnKind] = useState<ColumnKind | null>(null);
  const [lookalikesOpen, setLookalikesOpen] = useState(false);

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
    if ((anyLive && progressed) || (prev !== null && prev.live && !anyLive)) void refreshLoaded();
  }, [jobsSignature, anyLive, refreshLoaded]);

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

  // The drawer's invoker (the toolbar primary or the "+" header cell)
  // gets focus back on close: ref focus for an imperative gesture
  // outside a mount, per the house focus rules.
  const addColumnInvokerRef = useRef<HTMLElement | null>(null);
  // Only the AI kind keeps the drawer, where a prompt, a model,
  // outputs and tools have to be chosen; a plain column is named in
  // the grid.
  function openAddColumn(kind: ColumnKind) {
    if (kind !== "ai") {
      columns.startPending(kind);
      return;
    }
    addColumnInvokerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setAddColumnKind(kind);
  }
  function closeAddColumn() {
    setAddColumnKind(null);
    addColumnInvokerRef.current?.focus();
    addColumnInvokerRef.current = null;
  }

  async function removeColumn(column: ListColumn) {
    if (!(await columns.remove(column))) return;
    // The values left with the column, so the loaded rows still
    // carry a key the sheet no longer has a header for.
    await refreshLoaded();
  }

  async function submitBlankColumn(payload: BlankColumnPayload): Promise<ColumnOutcome> {
    const outcome = await columns.addBlank(payload);
    if (!outcome.ok) return outcome;
    closeAddColumn();
    toast.success(`Added the ${payload.label} column.`);
    router.refresh();
    return outcome;
  }

  async function submitAiColumn(payload: AiColumnPayload): Promise<ColumnOutcome> {
    const outcome = await columns.addAi(payload);
    if (!outcome.ok) return outcome;
    closeAddColumn();
    // The poll loop's promise settles only when the fill ENDS, so the
    // attach is fire-and-forget (it happens on the loop's first tick);
    // only the bounded row re-read is awaited.
    void fill.refresh();
    await refreshLoaded();
    router.refresh();
    return outcome;
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
    if (redirectIfUnauthenticated(res)) return null;
    if (res.status !== "ok") return res.message;
    // The new job and its pending outcomes exist only server-side:
    // re-attach the poll loop (fire-and-forget; its promise settles
    // when the fill ENDS) and re-read the loaded rows.
    void fill.refresh();
    await refreshLoaded();
    return null;
  }

  async function submitRename() {
    const next = titleDraft?.trim() ?? "";
    setTitleDraft(null);
    if (!next || next === detail.label) return;
    if (await columns.renameList(next)) router.refresh();
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

  return (
    // FILLS the page's pinned wrapper ([id]/page.tsx owns the
    // viewport calc and clips): three bands split the height; only
    // the grid band scrolls, the page itself never does.
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-hairline px-4 py-2">
        <div className="min-w-0">
          {titleDraft !== null ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submitRename();
              }}
            >
              <Input
                autoFocus
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={() => void submitRename()}
                onKeyDown={(e) => {
                  // Escape cancels: unmounting fires no blur, so nothing
                  // commits.
                  if (e.key === "Escape") setTitleDraft(null);
                }}
                className="max-w-xs py-1 text-sm font-semibold"
                aria-label="List name"
              />
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setTitleDraft(detail.label)}
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
          onReorder={columns.reorder}
          onRenameColumn={columns.rename}
          onDeleteColumn={removeColumn}
          pendingColumn={columns.pendingColumn}
          onNamePending={(label) => void columns.namePending(label)}
        />
        {hasMore && (
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
