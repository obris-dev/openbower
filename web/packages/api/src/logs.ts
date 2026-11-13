// Signals: a listener on the signals service and the sheet it fills, which
// the API calls a log. The client names outcomes as a result union; the
// caller decides what each means for the UI.

import {
  ListenerVersionWireSchema,
  ListenerWireSchema,
  LogDetailSchema,
  LogSummarySchema,
  LogsPageSchema,
  SyncKeyReconnectedSchema,
  type LogSummary as WireLogSummary,
  type SyncKeyReconnected,
} from "@bower/schema";
import { z } from "zod";

import { http, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";
import {
  TolerantFundingFilterSchema,
  TolerantMatchErrorSchema,
  type KnownSignal,
  type SignalSetup,
} from "./signals.ts";
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

/** Where the listener stands on the signals service: active, retrying (an
 * event failed and is tried again after a wait) or held (its saved setup
 * no longer fits the service: held until edited). An unknown one reads as
 * a client-only member, which claims nothing. */
export const LISTENER_STATUS = { ACTIVE: "active", RETRYING: "retrying", HELD: "held" } as const;
export const UNKNOWN_LISTENER_STATUS = "unknown_status" as const;
export type ListenerStatus = (typeof LISTENER_STATUS)[keyof typeof LISTENER_STATUS] | typeof UNKNOWN_LISTENER_STATUS;
const KNOWN_LISTENER_STATUSES = new Set<string>(ListenerWireSchema.shape.status.options);

const TolerantListenerSchema = ListenerWireSchema.extend({
  // The signal it follows, as the catalog names it: any, as on the log.
  signal: z.string(),
  status: z
    .string()
    .transform((status): ListenerStatus =>
      KNOWN_LISTENER_STATUSES.has(status) ? (status as ListenerStatus) : UNKNOWN_LISTENER_STATUS,
    ),
  error: TolerantMatchErrorSchema.nullable(),
  version: ListenerVersionWireSchema.extend({ filter: TolerantFundingFilterSchema }),
});
export const TolerantLogDetailSchema = LogDetailSchema.extend({
  sheet: TolerantSheetSummarySchema,
  status: TolerantLogStatusSchema,
  listener: TolerantListenerSchema.nullable(),
});
/** One signal with its listener as the signals service has it now (null
 * once the listener is gone). */
export type LogDetail = z.output<typeof TolerantLogDetailSchema>;
export type { SyncKeyReconnected };

/** What a create's caller reads: the new signal's sheet. The rest of the
 * 201 (the listener, with its own server-owned enums) is left unread, so
 * a value the signals service adds can never fail a create that landed,
 * which a person would retry into a second signal. */
export type CreatedLog = { sheet: SheetSummary };
const CreatedLogSchema = LogDetailSchema.pick({ sheet: true }).extend({ sheet: TolerantSheetSummarySchema });

/** One page of the signals a person set up, newest first (?after= the
 * previous page's next_cursor). */
export async function fetchLogsPage(after?: string) {
  const suffix = after ? `?after=${encodeURIComponent(after)}` : "";
  return http.get(`${apiRoutes.logs.index}${suffix}`, TolerantLogsPageSchema);
}

/** Create a signal: its listener on the signals service, and the sheet
 * it fills (201), whose arriving rows fill its AI columns. The account's
 * first signal mints its sync key. */
export async function createLog(
  body: { label: string; signal: KnownSignal } & SignalSetup,
): Promise<ApiResult<CreatedLog>> {
  return http.post(apiRoutes.logs.index, CreatedLogSchema, body);
}

/** One signal, its listener read live from the signals service. */
export async function fetchLog(id: string): Promise<ApiResult<LogDetail>> {
  return http.get(apiRoutes.logs.detail(id), TolerantLogDetailSchema);
}

/** A fresh sync key through this person, so the account's signals fill
 * again from where they stopped. The replaced key is named when it is
 * still live (another member minted it, or its revoke failed). */
export async function reconnectLogs(): Promise<ApiResult<SyncKeyReconnected>> {
  return http.post(apiRoutes.logs.reconnect, SyncKeyReconnectedSchema);
}

/** Delete a signal: its listener stops, then its sheet and rows go (204).
 * The signals service unreachable deletes nothing, so it can be retried. */
export async function deleteLog(id: string): Promise<ApiResult<null>> {
  return http.delete(apiRoutes.logs.detail(id));
}
