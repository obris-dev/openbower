// Signals: a listener on the signals service and the sheet it fills (the
// code calls the sheet a log). The client names outcomes as a result union;
// the caller decides what each means for the UI.

import {
  ListenerVersionWireSchema,
  ListenerWireSchema,
  SignalDetailSchema,
  SignalSummarySchema,
  SignalsPageSchema,
  SyncKeyReconnectedSchema,
  type SignalSummary as WireSignalSummary,
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
} from "./signal-types.ts";
import { TolerantSheetSummarySchema, type SheetSummary } from "./sheets.ts";

/** Where a signal stands, as the server names it: active (it fills),
 * reconnect (the account's sync key was refused; a person reconnects),
 * listener_gone (the signals service no longer has it: final), full (its
 * sheet holds as many rows as it may). */
export const SIGNAL_STATUS = {
  ACTIVE: "active",
  RECONNECT: "reconnect",
  LISTENER_GONE: "listener_gone",
  FULL: "full",
} as const;
// A status this bundle has not heard of reads as this client-only member,
// which renders no status: every real one claims something (that it fills,
// or what a person must do) a stale bundle cannot know.
export const UNKNOWN_SIGNAL_STATUS = "unknown_status" as const;
export type SignalStatus = (typeof SIGNAL_STATUS)[keyof typeof SIGNAL_STATUS] | typeof UNKNOWN_SIGNAL_STATUS;

const KNOWN_SIGNAL_STATUSES = new Set<string>(SignalSummarySchema.shape.status.options);
const TolerantSignalStatusSchema = z
  .string()
  .transform((status): SignalStatus => (KNOWN_SIGNAL_STATUSES.has(status) ? (status as SignalStatus) : UNKNOWN_SIGNAL_STATUS));

export type SignalSummary = Omit<WireSignalSummary, "sheet" | "status"> & { sheet: SheetSummary; status: SignalStatus };
export type SignalsPage = { items: SignalSummary[]; next_cursor: string | null };

export const TolerantSignalSummarySchema = SignalSummarySchema.extend({
  sheet: TolerantSheetSummarySchema,
  status: TolerantSignalStatusSchema,
});
export const TolerantSignalsPageSchema = SignalsPageSchema.extend({ items: z.array(TolerantSignalSummarySchema) });

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
export const TolerantSignalDetailSchema = SignalDetailSchema.extend({
  sheet: TolerantSheetSummarySchema,
  status: TolerantSignalStatusSchema,
  listener: TolerantListenerSchema.nullable(),
});
/** One signal with its listener as the signals service has it now (null
 * once the listener is gone). */
export type SignalDetail = z.output<typeof TolerantSignalDetailSchema>;
export type { SyncKeyReconnected };

/** What a create's caller reads: the new signal's sheet. The rest of the
 * 201 (the listener, with its own server-owned enums) is left unread, so
 * a value the signals service adds can never fail a create that landed,
 * which a person would retry into a second signal. */
export type CreatedSignal = { sheet: SheetSummary };
const CreatedSignalSchema = SignalDetailSchema.pick({ sheet: true }).extend({ sheet: TolerantSheetSummarySchema });

/** One page of the signals a person set up, newest first (?after= the
 * previous page's next_cursor). */
export async function fetchSignalsPage(after?: string) {
  const suffix = after ? `?after=${encodeURIComponent(after)}` : "";
  return http.get(`${apiRoutes.signals.index}${suffix}`, TolerantSignalsPageSchema);
}

/** Create a signal: its listener on the signals service, and the sheet
 * it fills (201), whose arriving rows fill its AI columns. The account's
 * first signal mints its sync key. */
export async function createSignal(
  body: { label: string; signal: KnownSignal } & SignalSetup,
): Promise<ApiResult<CreatedSignal>> {
  return http.post(apiRoutes.signals.index, CreatedSignalSchema, body);
}

/** One signal, its listener read live from the signals service. */
export async function fetchSignal(id: string): Promise<ApiResult<SignalDetail>> {
  return http.get(apiRoutes.signals.detail(id), TolerantSignalDetailSchema);
}

/** A fresh sync key through this person, so the account's signals fill
 * again from where they stopped. The replaced key is named when it is
 * still live (another member minted it, or its revoke failed). */
export async function reconnectSignals(): Promise<ApiResult<SyncKeyReconnected>> {
  return http.post(apiRoutes.signals.reconnect, SyncKeyReconnectedSchema);
}

/** The refusal of an edit to a signal whose listener the signals service
 * no longer has (409): the server has marked the signal gone, so a fresh
 * read shows what is left of it. */
export const LISTENER_GONE_CODE = "listener_gone";

/** What an edit changes: the name alone, or the setup whole (the service
 * saves a setup as a new version, so a rename never sends one). */
export type SignalEdit = { label: string } | ({ label?: string } & SignalSetup);

/** Edit a signal: its name (on its listener and its sheet), its setup, or
 * both; answers the signal as it now stands. */
export async function editSignal(id: string, body: SignalEdit): Promise<ApiResult<SignalDetail>> {
  return http.patch(apiRoutes.signals.detail(id), TolerantSignalDetailSchema, body);
}

/** Delete a signal: its listener stops, then its sheet and rows go (204).
 * The signals service unreachable deletes nothing, so it can be retried. */
export async function deleteSignal(id: string): Promise<ApiResult<null>> {
  return http.delete(apiRoutes.signals.detail(id));
}
