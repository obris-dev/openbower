"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronRight, Folder as FolderIcon, Plus } from "lucide-react";
import { Button, Card, Dropdown, DropdownButton, DropdownItem, DropdownMenu, EmptyState, Input, useToast } from "@bower/ui";
import {
  createFolder,
  createList,
  deleteFolder,
  deleteList,
  fetchFolders,
  fetchListsPage,
  importListCsv,
  renameFolder,
  updateList,
  webRoutes,
  type FolderSummary,
  type ListsPage,
  type ListSummary,
} from "@bower/api";

import { LinkButton } from "../../../_components/link-button";
import { ensureOk } from "@/lib/ensure-ok";
import { COLLAPSED_COOKIE } from "./constants";
import { RowMenu } from "./row-menu";

function day(iso: string): string {
  // Pinned locale + UTC: this renders on the server AND hydrates in the
  // browser, and any locale/timezone disagreement is a hydration error.
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

type Renaming = { kind: "list" | "folder"; id: string } | null;

function writeCollapsed(ids: Set<string>): void {
  const secure = window.location.protocol === "https:" ? "; secure" : "";
  document.cookie = `${COLLAPSED_COOKIE}=${[...ids].join(".")}; path=/; max-age=${365 * 24 * 3600}; samesite=lax${secure}`;
}

/** The home directory: every sheet, rendered in the SAME table language
 * as the sheets themselves (one idiom across index, sheet, and results).
 * Folders are sticky section headers inside the one table, not a nav
 * tree: they are the user's taxonomy over their work. */
export function Directory({
  initialLists,
  initialFolders,
  initialCollapsed,
  initialListsFailed,
  initialFoldersFailed,
}: {
  initialLists: ListsPage | null;
  initialFolders: FolderSummary[];
  initialCollapsed: string[];
  initialListsFailed: boolean;
  initialFoldersFailed: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [lists, setLists] = useState<ListSummary[]>(initialLists?.items ?? []);
  const [nextCursor, setNextCursor] = useState<string | null>(initialLists?.next_cursor ?? null);
  const [folders, setFolders] = useState<FolderSummary[]>(initialFolders);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(initialCollapsed));
  // These describe the SERVER RENDER's two legs; a successful
  // refresh() clears them. Deliberately one-way: a failing refetch
  // toasts (transient, retryable in place) rather than re-raising the
  // full failure surface, and later prop updates don't re-seed state.
  const [listsFailed, setListsFailed] = useState(initialListsFailed);
  const [foldersFailed, setFoldersFailed] = useState(initialFoldersFailed);
  const [renaming, setRenaming] = useState<Renaming>(null);
  const [renameValue, setRenameValue] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [importing, setImporting] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function refresh() {
    // Re-cover the depth the user had loaded (a mutation must not
    // snap a three-page view back to page one), and say so when the
    // refetch fails instead of silently keeping stale rows.
    const target = lists.length;
    const [pageRes, folderRes] = await Promise.all([fetchListsPage(), fetchFolders()]);
    // Sequential gates: a dual failure gets ONE toast, not two.
    if (!ensureOk(pageRes, toast)) return;
    if (!ensureOk(folderRes, toast)) return;
    let items = pageRes.data.items;
    let cursor = pageRes.data.next_cursor;
    while (cursor && items.length < target) {
      const more = await fetchListsPage(cursor);
      if (!ensureOk(more, toast)) return;
      items = [...items, ...more.data.items];
      cursor = more.data.next_cursor;
    }
    setLists(items);
    setNextCursor(cursor);
    setFolders(folderRes.data.items);
    setListsFailed(false);
    setFoldersFailed(false);
    // The server-rendered payload is stale now; refresh it so back-nav
    // does not resurrect the pre-mutation workspace.
    router.refresh();
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    const res = await fetchListsPage(nextCursor);
    setLoadingMore(false);
    if (!ensureOk(res, toast)) return;
    setLists((prev) => [...prev, ...res.data.items]);
    setNextCursor(res.data.next_cursor);
  }

  async function onFile(file: File) {
    setImporting(true);
    const res = await importListCsv(file);
    setImporting(false);
    if (!ensureOk(res, toast)) return;
    const { list, rows, skipped } = res.data;
    toast.success(
      skipped > 0
        ? `${rows.toLocaleString("en-US")} rows imported, ${skipped} skipped.`
        : `${rows.toLocaleString("en-US")} rows imported.`,
      list.label,
    );
    router.push(webRoutes.list(list.id));
  }

  async function quickAdd(kind: "list" | "folder") {
    const res = kind === "list" ? await createList("Untitled list") : await createFolder("New folder");
    if (!ensureOk(res, toast)) return;
    await refresh();
    // Land in rename with the default name ready to overtype.
    setRenaming({ kind, id: res.data.id });
    setRenameValue(res.data.label);
  }

  async function commitRename() {
    const target = renaming;
    setRenaming(null);
    if (!target) return;
    const label = renameValue.trim();
    if (!label) return;
    // An untouched rename is a no-op, not a PATCH plus a refetch.
    const current =
      target.kind === "list"
        ? lists.find((l) => l.id === target.id)?.label
        : folders.find((f) => f.id === target.id)?.label;
    if (label === current) return;
    const res =
      target.kind === "list" ? await updateList(target.id, { label }) : await renameFolder(target.id, label);
    if (!ensureOk(res, toast)) return;
    await refresh();
  }

  async function move(list: ListSummary, folderId: string) {
    const res = await updateList(list.id, { folder_id: folderId });
    if (!ensureOk(res, toast)) return;
    await refresh();
  }

  async function removeList(list: ListSummary) {
    const res = await deleteList(list.id);
    if (!ensureOk(res, toast)) return;
    toast.success("List deleted.", list.label);
    await refresh();
  }

  async function removeFolder(folder: FolderSummary) {
    const res = await deleteFolder(folder.id);
    if (!ensureOk(res, toast)) return;
    await refresh();
  }

  // A folder_id pointing nowhere (folders fetch failed, or a move-vs-
  // delete race) must not orphan the list off the page: unknown reads
  // as loose.
  const folderIds = new Set(folders.map((f) => f.id));
  const loose = lists.filter((l) => !l.folder_id || !folderIds.has(l.folder_id));
  const byFolder = new Map<string, ListSummary[]>();
  for (const list of lists) {
    if (list.folder_id && folderIds.has(list.folder_id))
      byFolder.set(list.folder_id, [...(byFolder.get(list.folder_id) ?? []), list]);
  }
  function renameCell(kind: "list" | "folder", id: string, label: string) {
    if (renaming?.kind === kind && renaming.id === id) {
      return (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void commitRename();
          }}
        >
          <Input
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onFocus={(e) => e.target.select()}
            onBlur={() => void commitRename()}
            onKeyDown={(e) => {
              // Escape cancels: unmounting the input fires no blur, so
              // nothing commits.
              if (e.key === "Escape") setRenaming(null);
            }}
            className="max-w-xs py-1"
            aria-label={`Rename ${kind}`}
          />
        </form>
      );
    }
    if (kind === "folder") {
      return (
        <span title={label} className="block min-w-0 truncate font-medium text-foreground">
          {label}
        </span>
      );
    }
    return (
      <Link
        href={webRoutes.list(id)}
        title={label}
        className="block truncate font-medium text-foreground hover:text-signal"
      >
        {label}
      </Link>
    );
  }

  function listRow(list: ListSummary, indent: boolean) {
    return (
      <tr key={list.id} className="align-middle">
        <td className={`w-full max-w-0 py-2.5 pr-4 ${indent ? "pl-10" : "pl-4"}`}>
          {renameCell("list", list.id, list.label)}
        </td>
        <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-muted">
          {list.row_count.toLocaleString("en-US")}
        </td>
        <td className="hidden whitespace-nowrap px-4 py-2.5 text-muted sm:table-cell">{day(list.updated_at)}</td>
        <td className="py-2.5 pl-2 pr-3 text-right">
          <RowMenu
            kind="list"
            folders={folders}
            currentFolderId={list.folder_id || undefined}
            onRename={() => {
              setRenaming({ kind: "list", id: list.id });
              setRenameValue(list.label);
            }}
            onMove={(folderId) => void move(list, folderId)}
            onDelete={() => void removeList(list)}
          />
        </td>
      </tr>
    );
  }

  const empty = lists.length === 0 && folders.length === 0;

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-foreground">All lists</h1>
          {empty && <p className="mt-1 text-sm text-muted">Sheets of anything: imported CSVs, saved Discover runs.</p>}
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={fileInput}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void onFile(file);
            }}
          />
          <Button loading={importing} onClick={() => fileInput.current?.click()}>
            Import CSV
          </Button>
          <Dropdown>
            <DropdownButton as={Button} variant="secondary" aria-label="New">
              <Plus aria-hidden className="h-4 w-4" />
            </DropdownButton>
            <DropdownMenu anchor="bottom end">
              <DropdownItem onClick={() => void quickAdd("list")}>New list</DropdownItem>
              <DropdownItem onClick={() => void quickAdd("folder")}>New folder</DropdownItem>
            </DropdownMenu>
          </Dropdown>
        </div>
      </div>

      {foldersFailed && !listsFailed && (
        // Folders down: whatever renders below is folderless; the
        // flattening must be SAID even when no lists loaded.
        <Card className="flex flex-wrap items-center justify-between gap-2 p-3">
          <p className="text-sm text-muted">Folders could not be loaded, so lists are shown without them.</p>
          <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>
            Retry
          </Button>
        </Card>
      )}
      {listsFailed && lists.length === 0 ? (
        // A down backend is NOT an empty workspace; say what happened.
        // Regardless of folders: headers over zero rows would read as
        // an emptied workspace.
        <EmptyState title="Your lists could not be loaded" subtitle="The server did not answer. Retry in a moment.">
          <Button size="sm" onClick={() => window.location.reload()}>
            Retry
          </Button>
        </EmptyState>
      ) : empty ? (
        <EmptyState
          title="No lists yet"
          subtitle="Import a CSV to start a sheet, or save a Discover search as a list from its results."
        >
          <Button size="sm" loading={importing} onClick={() => fileInput.current?.click()}>
            Import CSV
          </Button>
          <LinkButton size="sm" variant="ghost" href={webRoutes.discover}>
            Open Discover
          </LinkButton>
        </EmptyState>
      ) : (
        <Card className="p-0">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-faint">
                <th className="w-full py-3 pl-4 pr-4 font-medium">Name</th>
                <th className="px-4 py-3 text-right font-medium">Rows</th>
                <th className="hidden px-4 py-3 font-medium sm:table-cell">Updated</th>
                <th className="w-10 py-3 pl-2 pr-3" aria-label="Actions" />
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {loose.map((list) => listRow(list, false))}
              {folders.map((folder) => {
                const inside = byFolder.get(folder.id) ?? [];
                const isCollapsed = collapsed.has(folder.id);
                return [
                  <tr key={folder.id} className="bg-wash/50 align-middle">
                    <td className="w-full max-w-0 py-2 pl-2 pr-4">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <button
                          type="button"
                          aria-label={isCollapsed ? "Expand folder" : "Collapse folder"}
                          onClick={() => {
                            const next = new Set(collapsed);
                            if (next.has(folder.id)) next.delete(folder.id);
                            else next.add(folder.id);
                            setCollapsed(next);
                            // Prune deleted folders' ids at write time,
                            // or the cookie only ever grows.
                            writeCollapsed(new Set([...next].filter((id) => folderIds.has(id))));
                          }}
                          className="rounded p-0.5 text-faint hover:text-foreground"
                        >
                          {isCollapsed ? (
                            <ChevronRight aria-hidden className="h-4 w-4" />
                          ) : (
                            <ChevronDown aria-hidden className="h-4 w-4" />
                          )}
                        </button>
                        <FolderIcon aria-hidden className="h-4 w-4 shrink-0 text-faint" />
                        {renameCell("folder", folder.id, folder.label)}
                        {/* The server count on purpose: it is the folder's true size;
                            rows below it fill in as pages load. */}
                        <span className="shrink-0 text-xs text-faint">{folder.list_count}</span>
                      </span>
                    </td>
                    <td className="px-4 py-2" />
                    <td className="hidden px-4 py-2 sm:table-cell" />
                    <td className="py-2 pl-2 pr-3 text-right">
                      <RowMenu
                        kind="folder"
                        folders={[]}
                        containedCount={folder.list_count}
                        onRename={() => {
                          setRenaming({ kind: "folder", id: folder.id });
                          setRenameValue(folder.label);
                        }}
                        onDelete={() => void removeFolder(folder)}
                      />
                    </td>
                  </tr>,
                  ...(isCollapsed ? [] : inside.map((list) => listRow(list, true))),
                ];
              })}
            </tbody>
          </table>
          {nextCursor && (
            <div className="border-t border-hairline p-3 text-center">
              <Button size="sm" variant="ghost" loading={loadingMore} onClick={() => void loadMore()}>
                Load more
              </Button>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
