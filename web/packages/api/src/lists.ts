// Lists: the sheets CRUD the app backend serves locally. Same result
// -union philosophy as discover: the client names outcomes, the caller
// decides what each means for the UI.

import {
  FoldersListSchema,
  FolderSummarySchema,
  ImportResultSchema,
  ListRowsPageSchema,
  ListsPageSchema,
  ListSummarySchema,
  RowsAddedSchema,
  type FoldersList,
  type FolderSummary,
  type ImportResult,
  type ListColumn,
  type ListRowsPage,
  type ListsPage,
  type ListSummary,
  type RowsAdded,
} from "@bower/schema";

import { http, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";

// Rows paging, owned once: the server-rendered first page mirrors the
// backend's DEFAULT_ROWS_PAGE, the client pages its MAX_ROWS_PAGE.
export const ROWS_FIRST_PAGE = 50;
export const ROWS_PAGE_LIMIT = 200;

export type { FoldersList, FolderSummary, ImportResult, ListRowsPage, ListSummary, ListsPage, RowsAdded };
// Re-exported so app code never imports @bower/schema directly (the
// schema package has exactly one consumer: this one).
export type { ListColumn, ListRowWire } from "@bower/schema";

/** One definition of a column's numeric-ness: right-alignment in the
 * table and the export's formula-guard skip must agree. */
export function isNumericColumn(column: ListColumn): boolean {
  return column.type === "number" || column.type === "currency";
}

export async function fetchListsPage(after?: string): Promise<ApiResult<ListsPage>> {
  const suffix = after ? `?after=${encodeURIComponent(after)}` : "";
  return http.get(`${apiRoutes.lists.index}${suffix}`, ListsPageSchema);
}

// The walk's bound counts PAGES, not items: an item bound never
// terminates on an empty-page-with-cursor answer, and overshoots its
// stated cap by up to a page. Binary.
const MAX_LIST_PAGES = 8;

/** Every list, walking the keyset cursor to MAX_LIST_PAGES: dropdown
 * consumers must not silently truncate at one page, and when the
 * bound itself truncates, the CALLER hears it (no silent caps: a
 * console.warn reaches nobody). */
export async function fetchAllLists(): Promise<ApiResult<{ items: ListSummary[]; truncated: boolean }>> {
  const items: ListSummary[] = [];
  let after: string | undefined;
  let truncated = false;
  for (let page = 0; page < MAX_LIST_PAGES; page += 1) {
    const result = await fetchListsPage(after);
    if (result.status !== "ok") return result;
    items.push(...result.data.items);
    if (!result.data.next_cursor) break;
    after = result.data.next_cursor;
    truncated = page === MAX_LIST_PAGES - 1;
  }
  return { status: "ok", data: { items, truncated } };
}

export async function fetchList(id: string): Promise<ApiResult<ListSummary>> {
  return http.get(apiRoutes.lists.detail(id), ListSummarySchema);
}

export async function fetchListRows(
  id: string,
  opts: { after?: string; limit?: number } = {},
): Promise<ApiResult<ListRowsPage>> {
  const params = new URLSearchParams();
  if (opts.after) params.set("after", opts.after);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return http.get(`${apiRoutes.lists.rows(id)}${qs ? `?${qs}` : ""}`, ListRowsPageSchema);
}

export async function updateList(
  id: string,
  patch: { label?: string; folder_id?: string },
): Promise<ApiResult<ListSummary>> {
  return http.patch(apiRoutes.lists.detail(id), ListSummarySchema, patch);
}

export async function createList(label: string): Promise<ApiResult<ListSummary>> {
  return http.post(apiRoutes.lists.index, ListSummarySchema, { label });
}

export async function fetchFolders(): Promise<ApiResult<FoldersList>> {
  return http.get(apiRoutes.lists.folders, FoldersListSchema);
}

export async function createFolder(label: string): Promise<ApiResult<FolderSummary>> {
  return http.post(apiRoutes.lists.folders, FolderSummarySchema, { label });
}

export async function renameFolder(id: string, label: string): Promise<ApiResult<FolderSummary>> {
  return http.patch(apiRoutes.lists.folder(id), FolderSummarySchema, { label });
}

export async function deleteFolder(id: string): Promise<ApiResult<null>> {
  return http.delete(apiRoutes.lists.folder(id));
}

export async function deleteList(id: string): Promise<ApiResult<null>> {
  return http.delete(apiRoutes.lists.detail(id));
}

/** Manual append (bounded); the receipt is the contract's RowsAdded. */
export async function addListRows(id: string, rows: Record<string, string>[]): Promise<ApiResult<RowsAdded>> {
  return http.post(apiRoutes.lists.rows(id), RowsAddedSchema, { rows });
}

export async function importListCsv(file: File, label?: string): Promise<ApiResult<ImportResult>> {
  const form = new FormData();
  form.set("file", file);
  if (label) form.set("label", label);
  return http.post(apiRoutes.lists.import, ImportResultSchema, form);
}

export async function saveRunAsList(
  runId: string,
  opts: { label: string; limit?: number; exclude?: string[] },
): Promise<ApiResult<ListSummary>> {
  const body: Record<string, unknown> = { label: opts.label };
  if (opts.limit) body.limit = opts.limit;
  if (opts.exclude?.length) body.exclude = opts.exclude;
  return http.post(apiRoutes.discover.lookalikeRunSaveList(runId), ListSummarySchema, body);
}
