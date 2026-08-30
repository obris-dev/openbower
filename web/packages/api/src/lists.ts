// Lists: the sheets CRUD the app backend serves locally. Same result
// -union philosophy as discover: the client names outcomes, the caller
// decides what each means for the UI.

import {
  CellStateWireSchema,
  ListRowWireSchema,
  type ListRowWire,
  ColumnPromptWireSchema,
  FillRunPageSchema,
  FillRunWireSchema,
  FoldersListSchema,
  FolderSummarySchema,
  ImportResultSchema,
  ListColumnSchema,
  ListRowsPageSchema,
  ListsPageSchema,
  ListSummarySchema,
  RowsAddedSchema,
  WIRE_CONSTANTS,
  type AgentConfig,
  type ColumnFillSummary,
  type ColumnPromptWire,
  type FillRunPage,
  type FillRunWire,
  type FoldersList,
  type FolderSummary,
  type ImportResult,
  type ListColumn,
  type ListRowsPage,
  type ListsPage,
  type ListSummary,
  type RowsAdded,
} from "@bower/schema";

import { z } from "zod";

import { http, request, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";

// Rows paging, owned once: the server-rendered first page mirrors the
// backend's DEFAULT_ROWS_PAGE, the client pages its MAX_ROWS_PAGE.
export const ROWS_FIRST_PAGE = 50;
export const ROWS_PAGE_LIMIT = 200;

export type { ColumnFillSummary, ColumnPromptWire, FillRunPage, FillRunWire, FoldersList, FolderSummary, ImportResult, ListRowsPage, ListSummary, ListsPage, RowsAdded };
// Re-exported so app code never imports @bower/schema directly (the
// schema package has exactly one consumer: this one).
export type { FillError, ListColumn, ListRowWire } from "@bower/schema";

/** One cell's wire state, derived from the sidecar's own record so a
 * cause added server-side reaches every consumer through the regen:
 * `pending` is the in-flight shimmer, the rest are terminal blank
 * causes. Filled and not-attempted never travel (a value with no
 * state is filled; no state and no value is not attempted). */
export type CellStateWire = ListRowWire["states"][string];
export type CellState = CellStateWire["state"];
/** Tool -> the status code its door reported for the run that wrote
 * a cell ("open" for a tool that served). The vocabularies per tool
 * come off the contract (TOOL_STATUSES): base codes every tool shares
 * plus a tool's own; an unknown code renders the generic line. */
export type ToolStatuses = Record<string, string>;
export const TOOL_STATUSES = WIRE_CONSTANTS.TOOL_STATUSES;
export type ToolKey = keyof typeof TOOL_STATUSES;
export type ToolStatus = (typeof TOOL_STATUSES)[ToolKey][number];

// Fill facts off the contract document (never hand-copied): the
// heartbeat-staleness threshold the progress chip judges against, and
// the per-row retry patience and per-run search bound the consent
// footer cites.
export const ROW_LEASE_STALE_SECONDS = WIRE_CONSTANTS.ROW_LEASE_STALE_SECONDS;
// The SETTLED partition of CellState, off the contract document: the
// causes that hold (and never re-spend) until the config changes.
// The client derives its words-vs-dot split from this, never a
// hand-retyped list beside its copy.
// The whole cause vocabulary this bundle knows, read off the generated
// schema so it cannot drift from the contract.
// `.options`, the documented accessor its siblings use
// (COLUMN_TYPES, AGENT_PROVIDERS), not zod's internal `.def`
// shape: reaching into internals returns undefined on a minor
// bump rather than failing, and every cause would silently map
// to unknown.
export const CELL_STATES = CellStateWireSchema.shape.state.options;
export const SETTLED_CELL_STATES = WIRE_CONSTANTS.SETTLED_CELL_STATES;
export type SettledCellState = (typeof SETTLED_CELL_STATES)[number];

// The fill refusals a client CLASSIFIES by code, mirroring the
// server's FillErrorCode (lists/constants.py): the two the client
// RE-READS on (the row-count echo, so the next attempt echoes the new
// truth, and the stale column order, so the next move is judged
// against the set the sheet actually has) and the three whose
// offending surface is the OUTPUTS (they name what the fill would
// write, so the drawer marks that pane). Every other refusal renders
// through its verbatim detail alone and needs no name here.
export const ROW_COUNT_CHANGED_CODE = "row_count_changed";
export const COLUMN_ORDER_STALE_CODE = "column_order_stale";
export const COLUMN_COLLISION_CODE = "column_collision";
export const RESERVED_KEY_CODE = "reserved_key";
export const DERIVED_KEY_COLLISION_CODE = "derived_key_collision";

/** The AI-column add's POST body (the server's AiColumnRequest): the
 * quick config XOR an existing agent (its OUTPUTS are the columns;
 * no column label rides the request), and the row count the user
 * consented to (echoed; the server 409s when the count changed,
 * growth or shrinkage). `rows` scopes the fill to the sheet's first N
 * rows; omitted means every row. The server admits the true eligible
 * count either way. */
export type AiColumnBody = {
  config?: AgentConfig;
  agent_id?: string;
  confirmed_row_count: number;
  rows?: number;
  test_run_id?: string;
  concurrency?: number;
};

// Column-type OPTIONS derive from the generated contract (a type
// added or removed server-side reaches every consumer through the
// regen, never through a hand-retyped list).
export const COLUMN_TYPES = ListColumnSchema.shape.type.options;
export type ColumnType = ListColumn["type"];

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

// A cause this bundle has never heard of (a server ahead of the app)
// renders as the RETRYABLE dot: it promises the least, which is the
// safe thing to say about a cause we cannot name.
// The status enum is the SERVER's, and this bundle predates the next
// contract: strict-parsing it would turn one added member into a
// whole-page parse failure, which use-fill reads as a blip, so the
// poll backs off to its ceiling and the sheet says updates are not
// reaching it while the fill runs perfectly well. Widen the read and
// map the unknown member to the value that promises LEAST: RUNNING
// keeps the loop alive and claims nothing terminal.
const UNKNOWN_FILL_STATUS: FillRunWire["status"] = "running";
const FILL_STATUSES = new Set<string>(FillRunWireSchema.shape.status.options);
const TolerantFillRunWireSchema = FillRunWireSchema.extend({ status: z.string() });
const TolerantFillRunPageSchema = FillRunPageSchema.extend({ items: z.array(TolerantFillRunWireSchema) });

function knownStatus(status: string): FillRunWire["status"] {
  return (FILL_STATUSES.has(status) ? status : UNKNOWN_FILL_STATUS) as FillRunWire["status"];
}

/** A cause this bundle has never heard of. A CLIENT member, not one
 * of the server's: mapping it onto a real state made the cell assert
 * a fact it cannot know (the old choice, model_error, renders "The
 * model errored"). It sorts with the retryable causes, which is the
 * honest half: an unrecognised cause has not been shown to settle. */
export const UNKNOWN_CELL_STATE = "unknown_cause" as const;
export type RenderableCellState = CellState | typeof UNKNOWN_CELL_STATE;
/** One cell's state as the CLIENT holds it: the word (admitting the
 * unknown member) plus the tool statuses of the run that wrote it. */
export type RenderableCellStateWire = { state: RenderableCellState; tools: ToolStatuses };
/** A row as the CLIENT holds it: the states record admits the unknown
 * member the tolerant read produces, which the wire type cannot. */
export type RenderableListRow = Omit<ListRowWire, "states"> & { states: Record<string, RenderableCellStateWire> };
export type RenderableListRowsPage = Omit<ListRowsPage, "items"> & { items: RenderableListRow[] };
// Rows parse states TOLERANTLY: strict-parsing an unknown cause would
// fail the whole page, and a sheet that will not render is a worse
// answer than a cell whose dot is cautious. A bare string is the
// shape a server from before tool statuses shipped; it reads as that
// state with no tool facts.
const TolerantCellStateSchema = z.union([
  z.string(),
  z.object({ state: z.string(), tools: z.record(z.string(), z.string()).default({}) }),
]);
export const TolerantListRowsPageSchema = ListRowsPageSchema.extend({
  items: z.array(ListRowWireSchema.extend({ states: z.record(z.string(), TolerantCellStateSchema).default({}) })),
});
type TolerantCellState = z.infer<typeof TolerantCellStateSchema>;

export async function fetchListRows(
  id: string,
  opts: { after?: string; limit?: number } = {},
): Promise<ApiResult<RenderableListRowsPage>> {
  const params = new URLSearchParams();
  if (opts.after) params.set("after", opts.after);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const res = await http.get(`${apiRoutes.lists.rows(id)}${qs ? `?${qs}` : ""}`, TolerantListRowsPageSchema);
  if (res.status !== "ok") return res;
  return { ...res, data: renderablePage(res.data) };
}

/** The tolerant read's second half: narrow each state to something
 * this bundle can render. Exported because the SERVER fetch parses
 * the same endpoint, where a strict enum would fail the whole page
 * render rather than one poll. */
export function renderablePage(page: { items: { states: Record<string, TolerantCellState> }[] }): RenderableListRowsPage {
  const known = new Set<string>(CELL_STATES);
  const narrow = (entry: TolerantCellState): RenderableCellStateWire => {
    const state = typeof entry === "string" ? entry : entry.state;
    const tools = typeof entry === "string" ? {} : entry.tools;
    return { state: (known.has(state) ? state : UNKNOWN_CELL_STATE) as RenderableCellState, tools };
  };
  return {
    ...(page as unknown as RenderableListRowsPage),
    items: page.items.map((item) => ({
      ...(item as unknown as RenderableListRow),
      states: Object.fromEntries(Object.entries(item.states).map(([key, entry]) => [key, narrow(entry)])),
    })),
  };
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

/** Append one BLANK column (no fill): the key derives server-side
 * from the label, through the same rule fill columns use, so an AI
 * column that would land on the same key refuses rather than minting
 * a sibling. Refusals
 * (reserved key, duplicate, cap) surface through the funnel as the
 * server's verbatim detail plus code; the 200 body is the updated
 * summary. */
export async function postColumn(
  id: string,
  body: { label: string; type: ColumnType },
): Promise<ApiResult<ListSummary>> {
  return http.post(apiRoutes.lists.columns(id), ListSummarySchema, body);
}

/** Reorder the sheet's columns, sending the WHOLE key order.
 *
 * Not a move instruction: the server checks the submitted keys are a
 * permutation of the ones it holds, which a (from, to) pair cannot be
 * checked against, and refuses with 409 when a teammate has added or
 * deleted a column since this client read the sheet. The 200 body is
 * the updated summary, so the caller renders the SERVER's order
 * rather than trusting its own optimistic move. */
export async function reorderColumns(id: string, keys: string[]): Promise<ApiResult<ListSummary>> {
  return http.patch(apiRoutes.lists.columnOrder(id), ListSummarySchema, { keys });
}

/** Relabel one column. The KEY is the address and never changes: row
 * data is keyed on it server-side, so a key that followed the label
 * would strand every cell the column holds. */
export async function renameColumn(id: string, key: string, label: string): Promise<ApiResult<ListSummary>> {
  return http.patch(apiRoutes.lists.column(id, key), ListSummarySchema, { label });
}

/** Delete one column and everything in it. Any column, not only an AI
 * one. The body is the updated summary, so the sheet re-renders its
 * columns from the response. */
export async function deleteColumn(id: string, key: string): Promise<ApiResult<ListSummary>> {
  // Through `request` rather than `http.delete`, which is the
  // no-content form: this DELETE answers with the updated summary, so
  // the sheet re-renders its columns from the response like it does
  // after every other columns write.
  return request(apiRoutes.lists.column(id, key), ListSummarySchema, { method: "DELETE" });
}

/** Add an AI column and admit its fill in one server transaction;
 * returns the run envelope to attach to. Every refusal (row growth,
 * same-column active, caps, occupied-key collisions) surfaces through
 * the funnel as its server-written detail plus code. */
export async function postAiColumn(id: string, body: AiColumnBody): Promise<ApiResult<FillRunWire>> {
  return fillResult(await http.post(apiRoutes.lists.aiColumn(id), TolerantFillRunWireSchema, body));
}

/** Refill: the one recovery primitive. Starts a NEW run over the
 * column's rows without an answer (answered rows are excluded
 * server-side, never re-run and never re-billed; appended rows are
 * covered, so resume and fill-remaining are the same gesture). The
 * column names everything and the server takes a fresh config
 * snapshot; the one optional body fact is `rows`, scoping the new run
 * to the next N unanswered rows (omitted means all of them, and the
 * server owns the true eligible count either way). Refusals
 * (same-column active, caps, empty target) surface through the funnel
 * as the server's verbatim detail plus code; the 201 body is the run
 * envelope to attach to. */
export async function postFillRefill(
  id: string,
  columnKey: string,
  opts: { rows?: number; resumeFill?: string } = {},
): Promise<ApiResult<FillRunWire>> {
  // resumeFill bounds the new fill to THAT stopped fill's own
  // unresolved rows (Continue resumes; the extend gestures widen).
  // The option is named for the wire key it writes: DRF drops a body
  // key it does not declare without complaining, so a name that
  // drifts from the server's does not fail, it silently widens the
  // fill to the whole column against the user's metered key.
  const body: Record<string, unknown> = {};
  if (opts.rows !== undefined) body.rows = opts.rows;
  if (opts.resumeFill !== undefined) body.resume_fill = opts.resumeFill;
  return fillResult(
    await http.post(
      apiRoutes.lists.columnRefill(id, columnKey),
      TolerantFillRunWireSchema,
      Object.keys(body).length > 0 ? body : undefined,
    ),
  );
}

/** Edit the prompt of the agent filling a column, FROM the column
 * (ephemeral and roster agents alike; the column is the custody path
 * either way). A live run keeps its frozen snapshot, so the edit
 * reaches the NEXT run: on Continue, rows whose blanks settled under
 * the old prompt run again. Refusals surface through the funnel as
 * the server's verbatim detail plus code; the 200 body echoes the
 * stored prompt. */
export async function updateColumnPrompt(id: string, columnKey: string, prompt: string): Promise<ApiResult<ColumnPromptWire>> {
  return http.patch(apiRoutes.lists.columnPrompt(id, columnKey), ColumnPromptWireSchema, { prompt });
}

/** The column's CURRENT fill config (what a refill would run): the
 * prompt-peek surfaces read this, never a run's frozen snapshot. */
export async function getColumnPrompt(id: string, columnKey: string): Promise<ApiResult<ColumnPromptWire>> {
  return http.get(apiRoutes.lists.columnPrompt(id, columnKey), ColumnPromptWireSchema);
}


/** One fill envelope, status narrowed after a tolerant parse. */
function fillResult(res: ApiResult<z.infer<typeof TolerantFillRunWireSchema>>): ApiResult<FillRunWire> {
  if (res.status !== "ok") return res;
  return { ...res, data: { ...res.data, status: knownStatus(res.data.status) } };
}

/** One keyset page of the list's fill runs, newest first, ALL states
 * (a failed run is a first-class object carrying its error). */
export async function getFills(id: string, after?: string): Promise<ApiResult<FillRunPage>> {
  const suffix = after ? `?after=${encodeURIComponent(after)}` : "";
  const res = await http.get(`${apiRoutes.lists.fills(id)}${suffix}`, TolerantFillRunPageSchema);
  if (res.status !== "ok") return res;
  return {
    ...res,
    data: { ...res.data, items: res.data.items.map((item) => ({ ...item, status: knownStatus(item.status) })) },
  };
}

/** Stop a live fill; an already-terminal run no-ops. Returns the run
 * as the server now holds it. */
export async function postFillCancel(id: string, runId: string): Promise<ApiResult<FillRunWire>> {
  return fillResult(await http.post(apiRoutes.lists.fillCancel(id, runId), TolerantFillRunWireSchema));
}
