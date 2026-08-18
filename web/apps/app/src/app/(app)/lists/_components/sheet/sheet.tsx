"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Button,
  Card,
  Dropdown,
  DropdownButton,
  DropdownItem,
  DropdownMenu,
  Input,
  useToast,
} from "@bower/ui";
import { MoreHorizontal, Pencil } from "lucide-react";
import {
  deleteList,
  fetchListRows,
  ROWS_PAGE_LIMIT,
  loginUrl,
  updateList,
  webRoutes,
  type ListRowsPage,
  type ListSummary,
} from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";
import { FindLookalikes } from "./find-lookalikes";
import { downloadSheetCsv } from "./export";
import { SheetTable } from "./sheet-table";

/** The sheet: title (click to rename) over the rows table. Actions
 * follow the directory doctrine: the workflow verb is the PRIMARY
 * (Find lookalikes today; column work takes this slot when fills
 * arrive), the occasional verbs live behind the menu, and management
 * verbs beyond delete belong to the home directory. */
export function Sheet({ initialDetail, initialRows }: { initialDetail: ListSummary; initialRows: ListRowsPage }) {
  const router = useRouter();
  const toast = useToast();
  const [detail, setDetail] = useState(initialDetail);
  const [rows, setRows] = useState(initialRows.items);
  const [nextCursor, setNextCursor] = useState<string | null>(initialRows.next_cursor);
  const [loadingMore, setLoadingMore] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [label, setLabel] = useState(initialDetail.label);
  const [exporting, setExporting] = useState(false);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    const res = await fetchListRows(detail.id, { after: nextCursor, limit: ROWS_PAGE_LIMIT });
    setLoadingMore(false);
    if (!ensureOk(res, toast)) return;
    setRows((prev) => [...prev, ...res.data.items]);
    setNextCursor(res.data.next_cursor);
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

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
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
                className="max-w-sm text-lg font-semibold"
                aria-label="List name"
              />
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setRenaming(true)}
              title="Rename"
              className="group flex min-w-0 items-center gap-2 rounded text-left text-2xl font-bold text-foreground"
            >
              <span className="truncate group-hover:text-signal">{detail.label}</span>
              {/* Always visible (touch has no hover): the one hint the
                  title is editable. */}
              <Pencil aria-hidden className="h-4 w-4 shrink-0 text-faint group-hover:text-signal" />
            </button>
          )}
          <p className="mt-1 text-sm text-muted">{detail.row_count.toLocaleString("en-US")} rows</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {confirmingDelete ? (
            // Destruction confirms IN PLACE (the same slot the verb came
            // from), naming what dies; no modal.
            <>
              <span className="text-sm text-muted">Delete this list?</span>
              <Button size="sm" variant="danger" onClick={() => void remove()}>
                Delete
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmingDelete(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <FindLookalikes detail={detail} />
              <Dropdown>
                <DropdownButton
                  aria-label="List actions"
                  className="rounded-md p-2 text-faint hover:bg-wash hover:text-foreground"
                >
                  <MoreHorizontal aria-hidden className="h-5 w-5" />
                </DropdownButton>
                <DropdownMenu anchor="bottom end">
                  <DropdownItem disabled={exporting || detail.row_count === 0} onClick={() => void exportCsv()}>
                    {exporting ? "Exporting…" : "Export CSV"}
                  </DropdownItem>
                  <DropdownItem className="text-red-600" onClick={() => setConfirmingDelete(true)}>
                    Delete list
                  </DropdownItem>
                </DropdownMenu>
              </Dropdown>
            </>
          )}
        </div>
      </div>

      <Card className="p-0">
        <SheetTable columns={detail.columns} rows={rows} />
        {nextCursor && (
          <div className="border-t border-hairline p-3 text-center">
            <Button size="sm" variant="ghost" loading={loadingMore} onClick={() => void loadMore()}>
              Load more ({rows.length.toLocaleString("en-US")} of {detail.row_count.toLocaleString("en-US")})
            </Button>
          </div>
        )}
      </Card>
    </>
  );
}
