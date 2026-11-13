// Creating a signal: the catalog of signals a person can follow, a
// signal's recent events, and previews (a setup tried on events before it
// is saved), passed through to the signals service.

import {
  FundingEventSchema,
  FundingEventWireSchema,
  MatchErrorWireSchema,
  PreviewFailureWireSchema,
  PreviewRowWireSchema,
  PreviewRunWireSchema,
  SignalEventsPageSchema,
  SignalsCatalogSchema,
  type FundingFilter,
  type PreviewRowWire,
  type PreviewRunWire,
  type SignalEventsPage,
  type SignalKind,
  type SignalLimits,
} from "@bower/schema";
import { z } from "zod";

import { http, request, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";

export type { FundingFilter, SignalEventsPage, SignalKind, SignalLimits };

/** The signals this bundle has a form for. The catalog can offer more
 * than a bundle knows; those are left out, never shown as a form that
 * cannot say what it sends. */
export const SIGNAL = { FUNDING: "funding" } as const;
export type KnownSignal = (typeof SIGNAL)[keyof typeof SIGNAL];
const KNOWN_SIGNALS = new Set<string>(Object.values(SIGNAL));

const STAGE_SCHEMA = FundingEventSchema.shape.stage;
const STAGE_ENUM = STAGE_SCHEMA.options[0];
/** A funding round's stages, earliest first, as the contract names them. */
export const FUNDING_STAGES = STAGE_ENUM.options;
export type FundingStage = (typeof FUNDING_STAGES)[number];
const KNOWN_STAGES = new Set<string>(FUNDING_STAGES);
const INSTRUMENT_ENUM = FundingEventSchema.shape.instruments.element;
type FundingInstrument = (typeof INSTRUMENT_ENUM.options)[number];
const KNOWN_INSTRUMENTS = new Set<string>(INSTRUMENT_ENUM.options);

// An event's own value sets are the server's too: a stage it adds reads
// as no stage, and an instrument it adds is left out, so one new value
// never fails the page of events (or the preview carrying them).
const TolerantFundingEventSchema = FundingEventSchema.extend({
  stage: z
    .string()
    .nullable()
    .transform((stage): FundingStage | null => (stage !== null && KNOWN_STAGES.has(stage) ? (stage as FundingStage) : null)),
  instruments: z
    .array(z.string())
    .transform((instruments) => instruments.filter((each): each is FundingInstrument => KNOWN_INSTRUMENTS.has(each))),
});
const TolerantSignalEventSchema = FundingEventWireSchema.extend({ data: TolerantFundingEventSchema });
const TolerantSignalEventsPageSchema = SignalEventsPageSchema.extend({ items: z.array(TolerantSignalEventSchema) });

export type OfferedSignal = Omit<SignalKind, "signal"> & { signal: KnownSignal };
export type SignalsCatalog = { signals: OfferedSignal[]; limits: SignalLimits };

export const TolerantSignalsCatalogSchema = SignalsCatalogSchema.transform((catalog): SignalsCatalog => {
  const offered: OfferedSignal[] = [];
  for (const kind of catalog.signals) {
    if (KNOWN_SIGNALS.has(kind.signal)) {
      offered.push({ ...kind, signal: kind.signal as KnownSignal });
      continue;
    }
    // A signal added after this bundle: not offered until the app is
    // updated, and said so where a developer looks.
    console.error(`Unknown signal "${kind.signal}": not offered until the app is updated.`);
  }
  return { signals: offered, limits: catalog.limits };
});

// A preview's status and its rows' outcomes are the server's, and a
// bundle can predate the next one: each unknown member reads as a
// client-only one. An unknown status counts as a failed read in the poll
// (an added terminal one would otherwise poll forever), and an unknown
// outcome renders no verdict.
export const PREVIEW_STATUS = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETE: "complete",
  FAILED: "failed",
  CANCELED: "canceled",
} as const;
export const UNKNOWN_PREVIEW_STATUS = "unknown_status" as const;
export type PreviewStatus = (typeof PREVIEW_STATUS)[keyof typeof PREVIEW_STATUS] | typeof UNKNOWN_PREVIEW_STATUS;

export const PREVIEW_OUTCOME = {
  WAITING: "waiting",
  PASSED: "passed",
  MATCHED: "matched",
  UNSURE: "unsure",
  UNMATCHED: "unmatched",
  ERRORED: "errored",
} as const;
export const UNKNOWN_PREVIEW_OUTCOME = "unknown_outcome" as const;
export type PreviewOutcome =
  | (typeof PREVIEW_OUTCOME)[keyof typeof PREVIEW_OUTCOME]
  | typeof UNKNOWN_PREVIEW_OUTCOME;

const KNOWN_PREVIEW_STATUSES = new Set<string>(PreviewRunWireSchema.shape.status.options);
const KNOWN_PREVIEW_OUTCOMES = new Set<string>(PreviewRowWireSchema.shape.outcome.options);

export type PreviewRow = Omit<PreviewRowWire, "outcome"> & { outcome: PreviewOutcome };
export type PreviewRun = Omit<PreviewRunWire, "status" | "rows"> & { status: PreviewStatus; rows: PreviewRow[] };

// Why a preview or one of its rows failed: a kind the server adds reads as
// its unexpected kind, the one that claims nothing about the cause.
const FAILURE_KIND = PreviewFailureWireSchema.shape.kind;
type PreviewFailureKind = (typeof FAILURE_KIND.options)[number];
const KNOWN_FAILURE_KINDS = new Set<string>(FAILURE_KIND.options);
const TolerantPreviewFailureSchema = PreviewFailureWireSchema.extend({
  kind: z
    .string()
    .transform((kind): PreviewFailureKind =>
      KNOWN_FAILURE_KINDS.has(kind) ? (kind as PreviewFailureKind) : FAILURE_KIND.enum.unexpected,
    ),
});
const MATCH_ERROR_KIND = MatchErrorWireSchema.shape.kind;
type MatchErrorKind = (typeof MATCH_ERROR_KIND.options)[number];
const KNOWN_MATCH_ERROR_KINDS = new Set<string>(MATCH_ERROR_KIND.options);
const TolerantMatchErrorSchema = MatchErrorWireSchema.extend({
  kind: z
    .string()
    .transform((kind): MatchErrorKind =>
      KNOWN_MATCH_ERROR_KINDS.has(kind) ? (kind as MatchErrorKind) : MATCH_ERROR_KIND.enum.unexpected,
    ),
});

const TolerantPreviewRowSchema = PreviewRowWireSchema.extend({
  outcome: z
    .string()
    .transform((outcome): PreviewOutcome =>
      KNOWN_PREVIEW_OUTCOMES.has(outcome) ? (outcome as PreviewOutcome) : UNKNOWN_PREVIEW_OUTCOME,
    ),
  error: TolerantMatchErrorSchema.nullable(),
  event: TolerantSignalEventSchema,
});
export const TolerantPreviewRunSchema = PreviewRunWireSchema.extend({
  status: z
    .string()
    .transform((status): PreviewStatus =>
      KNOWN_PREVIEW_STATUSES.has(status) ? (status as PreviewStatus) : UNKNOWN_PREVIEW_STATUS,
    ),
  error: TolerantPreviewFailureSchema.nullable(),
  rows: z.array(TolerantPreviewRowSchema),
});

/** Whether a preview is still open: pending or running. The unknown
 * status is not; the poll counts it as a failed read instead. */
export function isPreviewOpen(run: PreviewRun): boolean {
  return run.status === PREVIEW_STATUS.PENDING || run.status === PREVIEW_STATUS.RUNNING;
}

/** The failure kind a busy judge ends a preview with; `retry_at` says when
 * to try again. */
export const PREVIEW_SLOW_DOWN_KIND = FAILURE_KIND.enum.slow_down;

/** A setup as the signals service takes it: the filter, with the judge's
 * prompt and threshold when there is a prompt. */
export type SignalSetup = {
  filter: Partial<FundingFilter>;
  prompt?: string;
  min_confidence?: number;
};

/** Every signal this bundle can offer, and the limits a setup is held to. */
export async function fetchSignalsCatalog(): Promise<ApiResult<SignalsCatalog>> {
  return http.get(apiRoutes.signals.catalog, TolerantSignalsCatalogSchema);
}

/** A signal's most recent events, newest first. */
export async function fetchSignalEvents(signal: KnownSignal, limit: number): Promise<ApiResult<SignalEventsPage>> {
  return http.get(`${apiRoutes.signals.events(signal)}?limit=${limit}`, TolerantSignalEventsPageSchema);
}

/** Try a setup on chosen events (202): complete at once when the filter
 * decides every event, otherwise poll it while the judge reads. */
export async function createSignalPreview(
  signal: KnownSignal,
  setup: SignalSetup,
  eventIds: string[],
): Promise<ApiResult<PreviewRun>> {
  return http.post(apiRoutes.signals.previews, TolerantPreviewRunSchema, { signal, ...setup, event_ids: eventIds });
}

export async function fetchSignalPreview(id: string): Promise<ApiResult<PreviewRun>> {
  return http.get(apiRoutes.signals.preview(id), TolerantPreviewRunSchema);
}

/** Cancel an open preview; a finished one is answered as it stands. */
export async function cancelSignalPreview(id: string): Promise<ApiResult<PreviewRun>> {
  return request(apiRoutes.signals.preview(id), TolerantPreviewRunSchema, { method: "DELETE" });
}
