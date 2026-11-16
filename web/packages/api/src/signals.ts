// Signals: a listener on the signals service and the sheet it fills (the
// code calls the sheet a log). The client names outcomes as a result union;
// the caller decides what each means for the UI.

import {
  HostedSourceSummarySchema,
  WebhookSourceSummarySchema,
  ListenerVersionWireSchema,
  ListenerWireSchema,
  CustomSignalCreatedSchema,
  SignalDetailSchema,
  SignalWebhookSchema,
  SignalSummarySchema,
  SignalsPageSchema,
  SyncKeyReconnectedSchema,
  WIRE_CONSTANTS,
  type SignalSummary as WireSignalSummary,
  type SignalWebhook,
  type SyncKeyReconnected,
} from "@bower/schema";
import { z } from "zod";

import { http, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";
import {
  TolerantFundingFilterSchema,
  TolerantMatchErrorSchema,
  type KnownSignalType,
  type SignalSetup,
} from "./signal-types.ts";
import {
  TolerantIngestSchemaSchema,
  TolerantSheetSummarySchema,
  type ColumnType,
  type IngestSchema,
  type SheetSummary,
} from "./sheets.ts";

/** Where a signal stands, as the server names it: active (it fills),
 * reconnect (the account's sync key was refused; a person reconnects),
 * listener_gone (the signals service no longer has it: final), full (its
 * sheet holds as many rows as it may), waiting (a custom signal whose
 * sheet holds no row). */
export const SIGNAL_STATUS = {
  ACTIVE: "active",
  RECONNECT: "reconnect",
  LISTENER_GONE: "listener_gone",
  FULL: "full",
  WAITING: "waiting",
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

/** What feeds a signal: at most one source of each kind, each carrying its
 * own kind's facts (hosted: a signal's listener, which the app reads;
 * webhook: a custom signal's endpoint, which a sender pushes rows to). A
 * kind this bundle has not heard of reads as a client-only member that
 * claims nothing; a known kind is read by its kind alone. */
export const SOURCE_KIND = { HOSTED: "hosted", WEBHOOK: "webhook" } as const;
export const UNKNOWN_SOURCE_KIND = "unknown_kind" as const;
// What the client reads of each kind; the rest is left unread, so a fact
// the server adds to a kind never fails a parse.
const HostedSourceSchema = HostedSourceSummarySchema.pick({ kind: true });
const WebhookSourceSchema = WebhookSourceSummarySchema.pick({ kind: true });
export type HostedSource = z.output<typeof HostedSourceSchema>;
export type WebhookSource = z.output<typeof WebhookSourceSchema>;
export type SourceSummary = HostedSource | WebhookSource | { kind: typeof UNKNOWN_SOURCE_KIND };
export const KNOWN_SOURCE_KINDS: ReadonlySet<string> = new Set<string>([
  HostedSourceSummarySchema.shape.kind.value,
  WebhookSourceSummarySchema.shape.kind.value,
]);
const UnknownSourceSchema = z
  .looseObject({ kind: z.string().refine((kind) => !KNOWN_SOURCE_KINDS.has(kind)) })
  .transform((): SourceSummary => ({ kind: UNKNOWN_SOURCE_KIND }));
const TolerantSourceSchema = z.union([HostedSourceSchema, WebhookSourceSchema, UnknownSourceSchema]);

/** What a signal is, besides a hosted type: one a sender pushes rows to. */
export const CUSTOM_SIGNAL_TYPE = WIRE_CONSTANTS.CUSTOM_SIGNAL_TYPE;

/** The signal's hosted source, if a hosted signal feeds it. */
export function hostedSource(log: { sources: SourceSummary[] }): HostedSource | undefined {
  return log.sources.find((source): source is HostedSource => source.kind === SOURCE_KIND.HOSTED);
}

/** What feeds a signal, as a page tells them apart: a hosted signal (its
 * listener, which the app reads and edits), a custom one (rows pushed to
 * its webhook), or neither, which this bundle cannot name and so claims
 * nothing about. */
export const SIGNAL_FEED = { HOSTED: "hosted", CUSTOM: "custom", UNKNOWN: "unknown" } as const;
export type SignalFeed = (typeof SIGNAL_FEED)[keyof typeof SIGNAL_FEED];

export function signalFeed(signal: { sources: SourceSummary[] }): SignalFeed {
  if (hostedSource(signal) !== undefined) return SIGNAL_FEED.HOSTED;
  const pushed = signal.sources.some((source) => source.kind === SOURCE_KIND.WEBHOOK);
  return pushed ? SIGNAL_FEED.CUSTOM : SIGNAL_FEED.UNKNOWN;
}

export type SignalSummary = Omit<WireSignalSummary, "sheet" | "status" | "sources"> & {
  sheet: SheetSummary;
  status: SignalStatus;
  sources: SourceSummary[];
};
export type SignalsPage = { items: SignalSummary[]; next_cursor: string | null };

export const TolerantSignalSummarySchema = SignalSummarySchema.extend({
  sheet: TolerantSheetSummarySchema,
  status: TolerantSignalStatusSchema,
  sources: z.array(TolerantSourceSchema),
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
  // The type it follows, as the catalog names it: any, as on the signal.
  type: z.string(),
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
  sources: z.array(TolerantSourceSchema),
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
  body: { label: string; type: KnownSignalType } & SignalSetup,
): Promise<ApiResult<CreatedSignal>> {
  return http.post(apiRoutes.signals.index, CreatedSignalSchema, body);
}

/** The columns a custom signal may declare: a sheet's cap (the server's
 * MAX_SHEET_COLUMNS). */
export const MAX_SIGNAL_COLUMNS = WIRE_CONSTANTS.MAX_SHEET_COLUMNS;

/** A custom signal's create answer: its sheet and its webhook URL. The
 * rest of the detail is left unread, as a hosted create's is. */
export type CreatedCustomSignal = { sheet: SheetSummary; webhook_url: string };
const CreatedCustomSignalSchema = CustomSignalCreatedSchema.pick({ sheet: true, webhook_url: true }).extend({
  sheet: TolerantSheetSummarySchema,
});

/** Create a custom signal: a sheet with the columns declared (each value
 * arrives with its row), fed by pushes to its webhook URL (201). Touches
 * no signals service. */
export async function createCustomSignal(body: {
  label: string;
  columns: { label: string; type: ColumnType }[];
}): Promise<ApiResult<CreatedCustomSignal>> {
  return http.post(apiRoutes.signals.index, CreatedCustomSignalSchema, { ...body, type: CUSTOM_SIGNAL_TYPE });
}

export type { SignalWebhook };

/** A custom signal's webhook URL, to copy any time. */
export async function fetchSignalWebhook(id: string): Promise<ApiResult<SignalWebhook>> {
  return http.get(apiRoutes.signals.webhook(id), SignalWebhookSchema);
}

/** The row schema a push to a custom signal is held to: the columns its
 * sender sets, with the keys a row is keyed on. */
export async function fetchSignalIngestSchema(id: string): Promise<ApiResult<IngestSchema>> {
  return http.get(apiRoutes.signals.ingest(id), TolerantIngestSchemaSchema);
}

/** A new webhook URL for a custom signal; the old one stops working at
 * once. Answers the URL (the rest of the detail is left unread). */
export async function rotateSignalWebhook(id: string): Promise<ApiResult<SignalWebhook>> {
  return http.post(apiRoutes.signals.webhookRotate(id), CustomSignalCreatedSchema.pick({ webhook_url: true }));
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

/** The refusal of a webhook URL read whose stored token no longer decrypts
 * (409: the encryption key changed since it was minted). Pushes to it still
 * work; Rotate URL replaces it with one that can be shown. */
export const WEBHOOK_UNREADABLE_CODE = "webhook_unreadable";

/** What an edit changes: the name alone, or the setup whole (the service
 * saves a setup as a new version, so a rename never sends one). */
export type SignalEdit = { label: string } | ({ label?: string } & SignalSetup);

/** Edit a signal: its name (on its listener and its sheet), its setup, or
 * both; answers the signal as it now stands. */
export async function editSignal(id: string, body: SignalEdit): Promise<ApiResult<SignalDetail>> {
  return http.patch(apiRoutes.signals.detail(id), TolerantSignalDetailSchema, body);
}

/** Delete a signal: a hosted signal's listener stops first, then its sheet
 * and rows go (204). The signals service unreachable deletes nothing, so
 * it can be retried. */
export async function deleteSignal(id: string): Promise<ApiResult<null>> {
  return http.delete(apiRoutes.signals.detail(id));
}
