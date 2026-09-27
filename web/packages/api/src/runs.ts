// Runs: the run-scoped endpoints (the preview's start, the run poll, run
// cancel). Same result-union philosophy as lists.

import { z } from "zod";

import type { AgentConfig } from "@bower/schema";
import { CellRunResultSchema, NodeRunWireSchema, WIRE_CONSTANTS, type CellRunResult, type NodeRunWire } from "@bower/schema";

import { http, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";

export type { CellRunResult, NodeRunWire };
export { CellRunResultSchema };

// The preview row's bounds, off the contract: the server REFUSES past
// them (never truncates), so inputs carry them as maxLength and the
// refusal stays unreachable from the UI.
export const TEST_ROW_MAX_KEYS = WIRE_CONSTANTS.TEST_ROW_MAX_KEYS;
export const TEST_KEY_MAX_LENGTH = WIRE_CONSTANTS.TEST_KEY_MAX_LENGTH;
export const TEST_VALUE_MAX_LENGTH = WIRE_CONSTANTS.TEST_VALUE_MAX_LENGTH;

// The OPEN partition of the run's status vocabulary, off the contract:
// the poll keeps polling while the status is one of these.
export const OPEN_NODE_RUN_STATES: ReadonlySet<string> = new Set(WIRE_CONSTANTS.OPEN_NODE_RUN_STATES);
// A run's deadness window, off the contract: a run's heartbeat FREEZES
// for the whole of its execution, so the quiet-run warning judges
// against this and never against the row-lease window (which would
// flag a legitimately running row).
export const NODE_RUN_STALE_SECONDS = WIRE_CONSTANTS.NODE_RUN_STALE_SECONDS;

// The status enum is the SERVER's, and this bundle can predate the
// next contract: strict-parsing it turns one added member into a
// permanent parse failure, which the preview poll reads as a blip, so
// the loop backs off forever with the Test button stuck busy. Widen
// the read and map an unknown member to a CLIENT member (the sheet's
// cell-cause rule in lists.ts), never onto a server state: the loop
// treats it as open (it claims nothing terminal) but can COUNT it, so
// an unheard-of terminal member ends the poll at the error bound
// instead of holding the Test button busy for good.
export const UNKNOWN_RUN_STATUS = "unknown_status" as const;
export type RenderableRunStatus = NodeRunWire["status"] | typeof UNKNOWN_RUN_STATUS;
/** A run as the CLIENT holds it: the wire shape, its status admitting
 * the unknown member the tolerant read produces. */
export type RenderableRun = Omit<NodeRunWire, "status"> & { status: RenderableRunStatus };
const RUN_STATUSES = new Set<string>(NodeRunWireSchema.shape.status.options);
const TolerantNodeRunSchema = NodeRunWireSchema.extend({ status: z.string() }).transform(
  (raw): RenderableRun => ({
    ...raw,
    status: (RUN_STATUSES.has(raw.status) ? raw.status : UNKNOWN_RUN_STATUS) as RenderableRunStatus,
  }),
);

/** Whether a run is still open (the poll's loop predicate): a known
 * open member, or the unknown one (which promises nothing terminal). */
export function isRunOpen(run: RenderableRun): boolean {
  return run.status === UNKNOWN_RUN_STATUS || OPEN_NODE_RUN_STATES.has(run.status);
}

/** Start a preview run of a DRAFTED config (saved or not) against one
 * hand-fed row: one run that owns its input, executed by the worker
 * like any other (the throwaway rides the real execution path).
 * Returns the run to poll; your own unclaimed runs are abandoned by
 * the start, a teammate's never touched. */
export async function postPreviewRun(config: AgentConfig, row: Record<string, string>): Promise<ApiResult<RenderableRun>> {
  return http.post(apiRoutes.runs.preview, TolerantNodeRunSchema, { config, row });
}

/** The poll leg: one run by id, its stored result once it finished.
 * Open-ended like every worker-supervised poll: the run terminates
 * server-side whatever this page can reach. */
export async function fetchRun(id: string): Promise<ApiResult<RenderableRun>> {
  return http.get(apiRoutes.runs.detail(id), TolerantNodeRunSchema);
}

/** Abandon a preview run that has not been claimed; a run already
 * running finishes on its own and keeps its result. */
export async function postRunCancel(id: string): Promise<ApiResult<RenderableRun>> {
  return http.post(apiRoutes.runs.cancel(id), TolerantNodeRunSchema);
}
