// Runs: the run-scoped endpoints (the bench's start, the run poll, run
// cancel). Same result-union philosophy as lists.

import { z } from "zod";

import type { AgentConfig } from "@bower/schema";
import { CellRunResultSchema, NodeRunWireSchema, WIRE_CONSTANTS, type CellRunResult, type NodeRunWire } from "@bower/schema";

import { http, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";

export type { CellRunResult, NodeRunWire };
export { CellRunResultSchema };

// The 409 code the server answers when a TEAMMATE's test is live
// (your own is superseded, never refused). Mirrors
// lists/constants.py BenchErrorCode.TEST_ACTIVE.
export const TEST_ACTIVE_CODE = "test_active";

// The bench row's bounds, off the contract: the server REFUSES past
// them (never truncates), so inputs carry them as maxLength and the
// refusal stays unreachable from the UI.
export const TEST_ROW_MAX_KEYS = WIRE_CONSTANTS.TEST_ROW_MAX_KEYS;
export const TEST_KEY_MAX_LENGTH = WIRE_CONSTANTS.TEST_KEY_MAX_LENGTH;
export const TEST_VALUE_MAX_LENGTH = WIRE_CONSTANTS.TEST_VALUE_MAX_LENGTH;

// The OPEN partition of the run's status vocabulary, off the contract:
// the poll keeps polling while the status is one of these.
export const OPEN_NODE_RUN_STATES: ReadonlySet<string> = new Set(WIRE_CONSTANTS.OPEN_NODE_RUN_STATES);

// The status enum is the SERVER's, and this bundle can predate the
// next contract: strict-parsing it turns one added member into a
// permanent parse failure, which the bench poll reads as a blip, so
// the loop backs off forever with the Test button stuck busy. Same
// tolerance, same mapping as the sheet's poll (lists.ts): widen the
// read and map an unknown member to the value that promises LEAST
// (PROCESSING keeps the loop alive and claims nothing terminal).
const UNKNOWN_RUN_STATUS: NodeRunWire["status"] = "processing";
const RUN_STATUSES = new Set<string>(NodeRunWireSchema.shape.status.options);
const TolerantNodeRunSchema = NodeRunWireSchema.extend({ status: z.string() }).transform(
  (raw): NodeRunWire => ({
    ...raw,
    status: (RUN_STATUSES.has(raw.status) ? raw.status : UNKNOWN_RUN_STATUS) as NodeRunWire["status"],
  }),
);

/** Whether a run is still open (the poll's loop predicate). */
export function isRunOpen(run: NodeRunWire): boolean {
  return OPEN_NODE_RUN_STATES.has(run.status);
}

/** Start a bench run of a DRAFTED config (saved or not) against one
 * hand-fed row: one run that owns its input, executed by the worker
 * like any other (the throwaway rides the real execution path).
 * Returns the run to poll; a teammate's live test answers 409 with
 * the server's own detail. */
export async function postBenchRun(config: AgentConfig, row: Record<string, string>): Promise<ApiResult<NodeRunWire>> {
  return http.post(apiRoutes.runs.bench, TolerantNodeRunSchema, { config, row });
}

/** The poll leg: one run by id, its stored result once it finished.
 * Open-ended like every worker-supervised poll: the run terminates
 * server-side whatever this page can reach. */
export async function fetchRun(id: string): Promise<ApiResult<NodeRunWire>> {
  return http.get(apiRoutes.runs.detail(id), TolerantNodeRunSchema);
}

/** Abandon a bench run that has not been claimed; a run already
 * running finishes on its own and keeps its result. */
export async function postRunCancel(id: string): Promise<ApiResult<NodeRunWire>> {
  return http.post(apiRoutes.runs.cancel(id), TolerantNodeRunSchema);
}
