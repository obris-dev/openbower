// Signals: a listener on the signals service and the sheet it fills, which
// the API calls a log. The client names outcomes as a result union; the
// caller decides what each means for the UI.

import { LogSummarySchema, LogsPageSchema, type LogSummary as WireLogSummary } from "@bower/schema";
import { z } from "zod";

import { http, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";
import { TolerantSheetSummarySchema, type SheetSummary } from "./sheets.ts";

/** Where a signal stands, as the server names it: active (it fills),
 * reconnect (the account's sync key was refused; a person reconnects),
 * listener_gone (the signals service no longer has it: final), full (its
 * sheet holds as many rows as it may). */
export const LOG_STATUS = {
  ACTIVE: "active",
  RECONNECT: "reconnect",
  LISTENER_GONE: "listener_gone",
  FULL: "full",
} as const;
// A status this bundle has not heard of reads as this client-only member,
// which renders no status: every real one claims something (that it fills,
// or what a person must do) a stale bundle cannot know.
export const UNKNOWN_LOG_STATUS = "unknown_status" as const;
export type LogStatus = (typeof LOG_STATUS)[keyof typeof LOG_STATUS] | typeof UNKNOWN_LOG_STATUS;

const KNOWN_LOG_STATUSES = new Set<string>(LogSummarySchema.shape.status.options);
const TolerantLogStatusSchema = z
  .string()
  .transform((status): LogStatus => (KNOWN_LOG_STATUSES.has(status) ? (status as LogStatus) : UNKNOWN_LOG_STATUS));

export type LogSummary = Omit<WireLogSummary, "sheet" | "status"> & { sheet: SheetSummary; status: LogStatus };
export type LogsPage = { items: LogSummary[]; next_cursor: string | null };

export const TolerantLogSummarySchema = LogSummarySchema.extend({
  sheet: TolerantSheetSummarySchema,
  status: TolerantLogStatusSchema,
});
export const TolerantLogsPageSchema = LogsPageSchema.extend({ items: z.array(TolerantLogSummarySchema) });

/** One page of the signals a person set up, newest first (?after= the
 * previous page's next_cursor). */
export async function fetchLogsPage(after?: string) {
  const suffix = after ? `?after=${encodeURIComponent(after)}` : "";
  return http.get(`${apiRoutes.logs.index}${suffix}`, TolerantLogsPageSchema);
}

/** Delete a signal: its listener stops, then its sheet and rows go (204).
 * The signals service unreachable deletes nothing, so it can be retried. */
export async function deleteLog(id: string): Promise<ApiResult<null>> {
  return http.delete(apiRoutes.logs.detail(id));
}
