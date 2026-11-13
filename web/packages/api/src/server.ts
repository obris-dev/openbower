// @bower/api/server: requests Next's SERVER makes on the user's behalf
// (forwarded session cookie, never credentials: "include"). The
// server-only guard turns any client-side import into a build error, so
// cookie-forwarding code cannot leak into the bundle.
import "server-only";

import type { ZodType } from "zod";

import { classifyMe, type MeResult } from "./auth.ts";
import {
  AgentsListSchema,
  AgentSummarySchema,
  ApiKeysListSchema,
  FoldersListSchema,
  type AgentsList,
  type AgentSummary,
  type ApiKeysList,
  type FoldersList,
} from "@bower/schema";
import { TolerantLogsPageSchema, type LogsPage } from "./logs.ts";
import { TolerantSignalsCatalogSchema, type SignalsCatalog } from "./signals.ts";
import { renderablePage, TolerantSheetRowsPageSchema, type RenderableSheetRowsPage, TolerantSheetsPageSchema, TolerantSheetDetailSchema, type SheetsPage, type SheetDetail } from "./sheets.ts";
import { fetchJson } from "./request.ts";
import { apiRoutes } from "./routes.ts";
import {
  TolerantWebhookDestinationsListSchema,
  TolerantWebhookDestinationWireSchema,
  type RenderableDestination,
} from "./webhooks.ts";

/** The server-side /me: the layout forwards the session cookie it
 * received, so the authed shell renders with the user already resolved
 * (no client spinner, no refetch). Never cached. */
export async function fetchMeResultWithCookie(cookieHeader: string): Promise<MeResult> {
  const fetched = await fetchJson(apiRoutes.auth.me, { headers: { cookie: cookieHeader }, cache: "no-store" });
  if (!fetched) return { status: "error" };
  return classifyMe(fetched.res, fetched.body);
}


// The outcomes a server component can act on: missing renders
// notFound, unauthenticated redirects to login, error renders an
// honest failure (never an empty state that lies about a down backend).
export type ServerFetchResult<T> =
  | { status: "ok"; data: T }
  | { status: "missing" }
  | { status: "unauthenticated" }
  | { status: "error" };

async function fetchParsedWithCookie<T>(
  path: string,
  cookieHeader: string,
  schema: ZodType<T>,
): Promise<ServerFetchResult<T>> {
  const fetched = await fetchJson(path, { headers: { cookie: cookieHeader }, cache: "no-store" });
  if (!fetched) return { status: "error" };
  const { res, body } = fetched;
  if (res.status === 401 || res.status === 403) return { status: "unauthenticated" };
  if (res.status === 404) return { status: "missing" };
  if (!res.ok) return { status: "error" };
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    if (process.env.NODE_ENV !== "production") console.error(`contract mismatch at ${path}:`, parsed.error);
    return { status: "error" };
  }
  return { status: "ok", data: parsed.data };
}

/** Server-side agents roster (unpaged; bounded by the backend cap). */
export async function fetchAgentsWithCookie(cookieHeader: string): Promise<ServerFetchResult<AgentsList>> {
  return fetchParsedWithCookie(apiRoutes.agents.index, cookieHeader, AgentsListSchema);
}

/** Server-side one agent (the edit page's first paint). */
export async function fetchAgentWithCookie(cookieHeader: string, id: string): Promise<ServerFetchResult<AgentSummary>> {
  return fetchParsedWithCookie(apiRoutes.agents.detail(id), cookieHeader, AgentSummarySchema);
}

/** Server-side webhook destinations roster (unpaged; bounded by the
 * backend cap). Tolerant: a delivery kind added server-side must not
 * blank the settings page for a deploy still serving this bundle. */
export async function fetchWebhooksWithCookie(
  cookieHeader: string,
): Promise<ServerFetchResult<{ items: RenderableDestination[] }>> {
  return fetchParsedWithCookie(apiRoutes.webhooks.index, cookieHeader, TolerantWebhookDestinationsListSchema);
}

/** Server-side machine API keys (unpaged; bounded by the identity
 * service's per-user cap). */
export async function fetchApiKeysWithCookie(cookieHeader: string): Promise<ServerFetchResult<ApiKeysList>> {
  return fetchParsedWithCookie(apiRoutes.auth.apiKeys, cookieHeader, ApiKeysListSchema);
}

/** Server-side one destination (the detail page's first paint). */
export async function fetchWebhookWithCookie(
  cookieHeader: string,
  id: string,
): Promise<ServerFetchResult<RenderableDestination>> {
  return fetchParsedWithCookie(apiRoutes.webhooks.detail(id), cookieHeader, TolerantWebhookDestinationWireSchema);
}

/** Server-side lists index page. */
export async function fetchSheetsPageWithCookie(cookieHeader: string): Promise<ServerFetchResult<SheetsPage>> {
  return fetchParsedWithCookie(apiRoutes.sheets.index, cookieHeader, TolerantSheetsPageSchema);
}

/** Server-side first page of the signals a person set up (the signals tab). */
export async function fetchLogsPageWithCookie(cookieHeader: string): Promise<ServerFetchResult<LogsPage>> {
  return fetchParsedWithCookie(apiRoutes.logs.index, cookieHeader, TolerantLogsPageSchema);
}

/** Server-side signals catalog (the Create signal page's first paint). */
export async function fetchSignalsCatalogWithCookie(cookieHeader: string): Promise<ServerFetchResult<SignalsCatalog>> {
  return fetchParsedWithCookie(apiRoutes.signals.catalog, cookieHeader, TolerantSignalsCatalogSchema);
}

/** Server-side folder set for the home directory. */
export async function fetchFoldersWithCookie(cookieHeader: string): Promise<ServerFetchResult<FoldersList>> {
  return fetchParsedWithCookie(apiRoutes.sheets.folders, cookieHeader, FoldersListSchema);
}

/** Server-side list detail; the page decides what each outcome renders. */
export async function fetchSheetWithCookie(cookieHeader: string, id: string): Promise<ServerFetchResult<SheetDetail>> {
  return fetchParsedWithCookie(apiRoutes.sheets.detail(id), cookieHeader, TolerantSheetDetailSchema);
}

/** Server-side first rows page, so the sheet paints with data. */
export async function fetchSheetRowsWithCookie(
  cookieHeader: string,
  id: string,
  limit: number,
): Promise<ServerFetchResult<RenderableSheetRowsPage>> {
  // TOLERANT here too, and for a sharper reason than on the client: a
  // strict enum on the server render fails the whole PAGE, not one
  // poll, so a cause added server-side would blank the sheet for
  // every deploy still serving the old bundle.
  const res = await fetchParsedWithCookie(
    `${apiRoutes.sheets.rows(id)}?limit=${limit}`,
    cookieHeader,
    TolerantSheetRowsPageSchema,
  );
  return res.status === "ok" ? { ...res, data: renderablePage(res.data) } : res;
}
