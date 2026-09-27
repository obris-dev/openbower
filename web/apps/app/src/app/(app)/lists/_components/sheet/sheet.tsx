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
  postColumnFill,
  type ListColumn,
  type WebhookColumn,
  webRoutes,
  type RenderableListRowsPage,
  type ListDetail,
  type WebhookColumnBody,
  type WebhookColumnPatchBody,
} from "@bower/api";

import { ConfirmDelete } from "../../../_components/confirm-delete";
import { ensureOk, redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { AddColumnMenuItems, type AddColumnKind } from "./add-column";
import { menuButtonId } from "./column-header";
import { UseAiDrawer, type AiColumnSubmission } from "./use-ai";
import { SendWebhookDrawer } from "./send-webhook";
import { FindLookalikes } from "./find-lookalikes";
import { downloadSheetCsv } from "./export";
import { FillsGlance, needsSearchProvider, useFill, type SearchProviderChoice } from "./fill";
import { SheetTable } from "./sheet-table";
import { pendingRefreshDelayMs, pendingSignature, readsAreTroubled } from "./lib/pending-refresh";
import { useColumns, type ColumnOutcome } from "./use-columns";
import { useRows } from "./use-rows";

/** The sheet, full-bleed under the shell's chrome in three bands: one
 * slim toolbar (click-to-rename title left; the actions right, column
 * work primary per the directory doctrine, occasional verbs behind the
 * menu), the grid as the page's ONE scroll region (sticky header row,
 * an IntersectionObserver sentinel driving the keyset loadMore with
 * the button kept as fallback), and a sticky status footer (row count
 * left, the fills glance right: a passive high-level read so filling
 * or failed columns cannot hide off a wide sheet's edge; per-column
 * progress belongs to the tracker row under the header).
 * Three hooks own the three kinds of state (the summary and its
 * columns, the rows on screen, the fill attachment); this component
 * composes their reactions to each other and renders. */
export function Sheet({ initialDetail, initialRows }: { initialDetail: ListDetail; initialRows: RenderableListRowsPage }) {
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
  // Add column menu), so half-open states are unrepresentable and the
  // two drawers (the AI column, the Send webhook column) can never be
  // open together.
  const [openDrawer, setOpenDrawer] = useState<{ kind: "ai" } | { kind: "webhook"; column: ListColumn | null } | null>(
    null,
  );
  const [lookalikesOpen, setLookalikesOpen] = useState(false);

  // A cell whose row's run had a degraded web search composes the
  // deployment's search provider into its popover (the paid-provider nudge
  // belongs only to the free provider), fetched once and only when such a
  // cell is on screen: a sheet with none never pays for the catalog.
  // Absence degrades to the bare sentence.
  const [searchProvider, setSearchProviderChoice] = useState<SearchProviderChoice>(null);
  // The predicate lives with the nudge copy it serves (cell-state):
  // one spelling for the fetch gate and the renderer.
  const providerNeeded = rows.some((row) => Object.values(row.states ?? {}).some((entry) => needsSearchProvider(entry.tools)));
  useEffect(() => {
    if (!providerNeeded || searchProvider !== null) return;
    let superseded = false;
    async function load() {
      const res = await fetchAgentCatalog();
      if (!superseded && res.status === "ok") setSearchProviderChoice(res.data.search_provider);
    }
    void load();
    return () => {
      superseded = true;
    };
  }, [providerNeeded, searchProvider]);

  // Rows re-read when a live run progressed (attempted moved), on
  // first sight of one, and once on the last-live-to-terminal edge:
  // the poll ships LIVE runs only, so a run finishing IS the live set
  // shrinking, and cells written between polls land in that final
  // read. The summaries ride the signature so a status flip with no
  // counter movement still lands. Terminal history on mount triggers
  // nothing: the server rendered those rows fresh.
  const runsSignature = [
    ...fill.runs.map((run) => `${run.id}:${run.counters.attempted}`),
    ...fill.summaries.map((summary) => `${summary.column_key}:${summary.current_fill_id}:${summary.current_status}`),
  ].join(" ");
  const anyLive = fill.runs.length > 0;
  const prevRunsRef = useRef<{ signature: string; live: boolean } | null>(null);
  useEffect(() => {
    if (!runsSignature) return;
    const prev = prevRunsRef.current;
    prevRunsRef.current = { signature: runsSignature, live: anyLive };
    const progressed = prev === null || prev.signature !== runsSignature;
    if ((anyLive && progressed) || (prev !== null && prev.live && !anyLive)) void refreshLoaded();
  }, [runsSignature, anyLive, refreshLoaded]);

  // Pending cells no fill reports (an autofill on arrived rows, a
  // webhook waiting for its window) re-read on their own schedule,
  // backing off while the pending set holds still and restarting when
  // it moves; a live fill's poll already re-reads, so this stands down
  // while one runs. Stops when nothing reads pending.
  // A read that keeps failing is surfaced the way the fill poll's is
  // (one line, the cells holding still), never swallowed: a cell would
  // otherwise shimmer with no sign the page has lost the server.
  const pending = pendingSignature(rows);
  const [pendingTrouble, setPendingTrouble] = useState(false);
  useEffect(() => {
    if (!pending || anyLive) return;
    let attempt = 0;
    let failures = 0;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    function schedule() {
      timer = setTimeout(async () => {
        const read = await refreshLoaded();
        if (stopped) return;
        failures = read ? 0 : failures + 1;
        setPendingTrouble(readsAreTroubled(failures));
        attempt += 1;
        schedule();
      }, pendingRefreshDelayMs(attempt));
    }
    schedule();
    return () => {
      stopped = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [pending, anyLive, refreshLoaded]);
  // The page's ONE trouble fact, whichever loop saw it. The re-read
  // loop's trouble counts only while that loop runs (a pending cell,
  // no live fill): its last word is stale the moment it stands down.
  const pollTrouble = fill.pollTrouble || (pendingTrouble && Boolean(pending) && !anyLive);

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
  function openAddColumn(kind: AddColumnKind) {
    if (kind !== "ai" && kind !== "webhook") {
      columns.startPending(kind);
      return;
    }
    addColumnInvokerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setOpenDrawer(kind === "ai" ? { kind } : { kind, column: null });
  }
  // The same drawer on an existing webhook column, prefilled from its config.
  function openEditWebhook(column: WebhookColumn) {
    // The menu item that was clicked is unmounting; the menu button
    // that opened it survives and is where focus belongs afterwards.
    addColumnInvokerRef.current = document.getElementById(menuButtonId(column.key));
    setOpenDrawer({ kind: "webhook", column });
  }
  function closeAddColumn() {
    setOpenDrawer(null);
    addColumnInvokerRef.current?.focus();
    addColumnInvokerRef.current = null;
  }

  async function removeColumn(column: ListColumn): Promise<ColumnOutcome> {
    const outcome = await columns.remove(column);
    if (!outcome.ok) return outcome;
    // The values left with the column, so the loaded rows still
    // carry a key the sheet no longer has a header for.
    await refreshLoaded();
    return outcome;
  }

  // The webhook column is a structural write: nothing starts and no
  // row changes, so the summary echo is the whole reaction.
  async function submitWebhookColumn(body: WebhookColumnBody): Promise<ColumnOutcome> {
    const outcome = await columns.addWebhook(body);
    if (!outcome.ok) return outcome;
    closeAddColumn();
    router.refresh();
    return outcome;
  }

  async function saveWebhookColumn(key: string, body: WebhookColumnPatchBody): Promise<ColumnOutcome> {
    const outcome = await columns.saveWebhook(key, body);
    if (outcome.ok) closeAddColumn();
    return outcome;
  }

  // Two requests: the create, then the fill. A refused create keeps
  // the drawer open on its refusal (the ask is what has to change).
  // Once the columns exist the drawer's work is done, so it closes
  // before the fill is asked for, and a refused fill is a toast on the
  // sheet: the column stays, fillable from its tracker once the cause
  // is fixed. A submission with no fill (a sheet with no rows) stops at
  // the create; the new column's tracker still needs its summary read.
  async function submitAiColumn({ body, fill: scope }: AiColumnSubmission): Promise<ColumnOutcome> {
    const added = await columns.addAi(body);
    if (!added.ok) return added;
    closeAddColumn();
    if (scope === null) {
      void fill.refresh();
      router.refresh();
      return { ok: true };
    }
    const refusal = await fillColumn(added.key, { maxRowCount: scope.maxRowCount });
    if (refusal) {
      // The create gave the new columns their summaries; a refused fill
      // re-attaches nothing, so read them here or the tracker holds its
      // loading state until a reload.
      void fill.refresh();
      toast.error(refusal, "Fill not started");
    }
    router.refresh();
    return { ok: true };
  }

  // A fill is a NEW run over the rows its agent never attempted (all of
  // them, or the next `rows` when the scoped ask named a count). The
  // COLUMN comes from the surface the user clicked and names the agent,
  // whose columns fill together. A refusal returns as the server's
  // verbatim detail for the caller to render.
  async function fillColumn(columnKey: string, opts: { maxRowCount?: number } = {}): Promise<string | null> {
    if (!columnKey) return GENERIC_FAILURE;
    const res = await postColumnFill(detail.id, columnKey, { max_row_count: opts.maxRowCount });
    if (redirectIfUnauthenticated(res)) return null;
    if (res.status !== "ok") return res.message;
    // The new run and its pending outcomes exist only server-side:
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

      <UseAiDrawer
        open={openDrawer?.kind === "ai"}
        onClose={closeAddColumn}
        rowCount={detail.row_count}
        columns={detail.columns}
        onSubmit={submitAiColumn}
      />
      <SendWebhookDrawer
        open={openDrawer?.kind === "webhook"}
        column={openDrawer?.kind === "webhook" ? openDrawer.column : null}
        onClose={closeAddColumn}
        onAdd={submitWebhookColumn}
        onSave={saveWebhookColumn}
        onAddAiColumn={() => {
          closeAddColumn();
          openAddColumn("ai");
        }}
        listId={detail.id}
        listLabel={detail.label}
        columns={detail.columns}
        sampleRows={rows.map((row) => ({ id: row.id, data: row.data }))}
      />

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto">
        <SheetTable
          columns={detail.columns}
          rows={rows}
          searchProvider={searchProvider}
          fills={{
            listId: detail.id,
            runs: fill.runs,
            summaries: fill.summaries,
            pollTrouble,
            rowCount: detail.row_count,
            entryActionIds: detail.entry_action_ids,
            onStop: fill.stop,
            onFill: fillColumn,
          }}
          onAddColumn={openAddColumn}
          onReorder={columns.reorder}
          onRenameColumn={columns.rename}
          onDeleteColumn={removeColumn}
          onEditWebhook={openEditWebhook}
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
            below sm); the glance is one short line by design, so the
            band never wraps. */}
        <p className="shrink-0 whitespace-nowrap text-xs text-muted">
          {detail.row_count.toLocaleString("en-US")}
          <span className="hidden sm:inline">{" rows"}</span>
        </p>
        <div className="flex min-w-0 flex-col items-end gap-1">
          <FillsGlance summaries={fill.summaries} liveRunIds={fill.runs.map((run) => run.id)} />
          {pollTrouble && (
            // Client-only fact, phrased as one: the page cannot see the
            // server, so it claims nothing about the fill itself. It is
            // the PAGE's trouble, so it renders once, under the band
            // beside the glance (which is passive and cannot carry it).
            <p className="text-xs text-warning">{"Progress updates aren't reaching this page; still retrying."}</p>
          )}
        </div>
      </div>
    </div>
  );
}
