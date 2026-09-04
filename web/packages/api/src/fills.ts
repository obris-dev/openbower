// Fills: the run-scoped endpoints (the bench's test-fill create, the
// run poll, run cancel). Same result-union philosophy as lists.

import { z } from "zod";

import type { AgentConfig } from "@bower/schema";
import {
  CellRunResultSchema,
  FillRunDetailSchema,
  WIRE_CONSTANTS,
  type CellRunResult,
  type FillRunDetail,
} from "@bower/schema";

import { http, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";

export type { CellRunResult, FillRunDetail };
export { CellRunResultSchema };

// The 409 code the server answers when a TEAMMATE's test is live
// (your own is superseded, never refused). Mirrors
// lists/constants.py FillErrorCode.TEST_ACTIVE.
export const TEST_ACTIVE_CODE = "test_active";

// The bench row's bounds, off the contract: admission REFUSES past
// them (never truncates), so inputs carry them as maxLength and the
// refusal stays unreachable from the UI.
export const TEST_ROW_MAX_KEYS = WIRE_CONSTANTS.TEST_ROW_MAX_KEYS;
export const TEST_KEY_MAX_LENGTH = WIRE_CONSTANTS.TEST_KEY_MAX_LENGTH;
export const TEST_VALUE_MAX_LENGTH = WIRE_CONSTANTS.TEST_VALUE_MAX_LENGTH;

// The status and kind enums are the SERVER's, and this bundle can
// predate the next contract: strict-parsing them turns one added
// member into a permanent parse failure, which the bench poll reads
// as a blip, so the loop backs off forever with the Test button
// stuck busy. Same tolerance, same mapping as the sheet's poll
// (lists.ts): widen the read and map an unknown member to the value
// that promises LEAST. Two independent decisions ride the widening:
// an ABSENT key takes the CONTRACT's declared default; an UNKNOWN
// member takes the least-promising value (RUNNING keeps the loop
// alive and claims nothing terminal; TEST claims no sheet).
const UNKNOWN_DETAIL_STATUS: FillRunDetail["status"] = "running";
const UNKNOWN_DETAIL_KIND: FillRunDetail["kind"] = "test";
const DETAIL_STATUSES = new Set<string>(FillRunDetailSchema.shape.status.options);
const DETAIL_KINDS = new Set<string>(FillRunDetailSchema.shape.kind.unwrap().options);
const TolerantFillRunDetailSchema = FillRunDetailSchema.extend({
  status: z.string(),
  kind: z.string().default("normal"),
}).transform(
  (raw): FillRunDetail => ({
    ...raw,
    status: (DETAIL_STATUSES.has(raw.status) ? raw.status : UNKNOWN_DETAIL_STATUS) as FillRunDetail["status"],
    kind: (DETAIL_KINDS.has(raw.kind) ? raw.kind : UNKNOWN_DETAIL_KIND) as FillRunDetail["kind"],
  }),
);

/** Start a test run of a DRAFTED config (saved or not) against one
 * hand-fed row: a one-row FILL in test mode, run by the worker like
 * any other (the throwaway rides the real execution path). Returns
 * the run envelope to poll; a teammate's live test answers 409 with
 * the server's own detail. Deliberately NOT resumable. */
export async function postTestFill(
  config: AgentConfig,
  row: Record<string, string>,
): Promise<ApiResult<FillRunDetail>> {
  return http.post(apiRoutes.fills.test, TolerantFillRunDetailSchema, { config, row });
}

/** The poll leg: one run by id, its stored result once a test run
 * completes. Open-ended like every worker-supervised poll: the run
 * terminates server-side whatever this page can reach. */
export async function fetchFillRun(id: string): Promise<ApiResult<FillRunDetail>> {
  return http.get(apiRoutes.fills.detail(id), TolerantFillRunDetailSchema);
}

/** Cancel a run wherever it is scoped (an inline test fill has no
 * list for the list-scoped cancel). */
export async function postFillRunCancel(id: string): Promise<ApiResult<FillRunDetail>> {
  return http.post(apiRoutes.fills.cancel(id), TolerantFillRunDetailSchema);
}
