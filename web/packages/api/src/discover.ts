// The discover domain client: look-alike queries through the app proxy.
// Every call rides the session cookie; the proxy forwards the IdP access
// token to the data service and owns refresh-and-retry, so the browser
// never sees a token or talks to the data origin.

import { LookalikeListResponseSchema, type LookalikeListResponse } from "@bower/schema";

import { errorDetail, fetchJson } from "./request.ts";
import { apiRoutes } from "./routes.ts";

// Re-exported for consumers: components type against @bower/api (the
// schema package has exactly one consumer, this one).
export type { LookalikeItem, LookalikeListResponse } from "@bower/schema";

/** The query the proxy accepts: exactly one seed source, or a cursor into
 * an existing run's pages. */
export type LookalikeQuery = {
  domains?: string[];
  // Seeding from a list: name the list and which column holds the
  // identifiers; the backend extracts + normalizes the values and the
  // query proceeds down the one domains path.
  list_id?: string;
  identifier_key?: string;
  limit?: number;
  cursor?: string;
};

// The classification union, mirroring MeResult's philosophy: the CALLER
// decides what each outcome means for the UI, the client only names them.
//  - ok: a complete run's page.
//  - computing: the run is pending/running; poll `runId`.
//  - unauthenticated: no live session (401); a fresh login fixes it.
//  - reauth: the session is live but predates data access (403); only a
//    fresh login can mint the missing scope, so it lands with
//    unauthenticated in the UI while staying distinguishable here.
//  - error: everything else (network, 5xx, contract break, failed run),
//    with a user-facing message.
export type LookalikesResult =
  | { status: "ok"; data: LookalikeListResponse }
  | { status: "computing"; runId: string; unresolvedDomains: string[] }
  | { status: "unauthenticated" }
  | { status: "reauth" }
  | { status: "error"; message: string; terminal?: boolean };

const GENERIC_FAILURE = "The search failed. Please try again.";

/** One page of look-alike results, everywhere: the query POST, the poll
 * leg (forwarded as ?limit=), and load-more, so a warm cohort and a cold
 * one answer the same page size. */
export const LOOKALIKE_PAGE_LIMIT = 50;

// The terminal run statuses, pinned to the CONTRACT's closed union: a
// typo here is a compile error, and an unknown status off the wire fails
// zod parsing into the error state (never "keep polling"). canceled is
// terminal by request; a fresh query revives the run.
const RUN_STATUS = {
  Complete: "complete",
  Failed: "failed",
  Canceled: "canceled",
} as const satisfies Record<string, LookalikeListResponse["status"]>;

function classify(res: Response, body: unknown): LookalikesResult {
  if (res.status === 401) return { status: "unauthenticated" };
  if (res.status === 403) return { status: "reauth" };
  if (!res.ok && res.status !== 202) {
    const detail = errorDetail(body);
    // 400s carry the upstream's human-readable reason (e.g. "at least 2
    // seeds must resolve in the universe"); pass it through.
    return { status: "error", message: res.status === 400 && detail ? detail : GENERIC_FAILURE };
  }
  const parsed = LookalikeListResponseSchema.safeParse(body);
  if (!parsed.success) return { status: "error", message: GENERIC_FAILURE };
  const data = parsed.data;
  if (data.status === RUN_STATUS.Complete) return { status: "ok", data };
  // terminal: the RUN ended (failed/canceled), as opposed to the POLL
  // failing; pollers surface it immediately instead of burning transient
  // retries on a state that cannot change by asking again.
  if (data.status === RUN_STATUS.Failed)
    return { status: "error", message: data.detail || GENERIC_FAILURE, terminal: true };
  // Terminal by request (ours, or another caller of the same global run):
  // never "keep polling".
  if (data.status === RUN_STATUS.Canceled)
    return { status: "error", message: "The search was canceled.", terminal: true };
  if (!data.run_id) return { status: "error", message: GENERIC_FAILURE };
  // Carry the unresolved list with the computing signal: it is computed
  // at resolve time and NOT stored on the (globally shared) run row, so
  // the poll envelope cannot restore it; the caller must.
  return { status: "computing", runId: data.run_id, unresolvedDomains: data.unresolved_domains };
}

/** POST the query. A cold cohort answers `computing`; poll with
 * `fetchLookalikeRunResult` until terminal. */
export async function fetchLookalikesResult(query: LookalikeQuery): Promise<LookalikesResult> {
  const fetched = await fetchJson(apiRoutes.discover.lookalikes, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(query),
  });
  if (!fetched) return { status: "error", message: "Could not reach the server." };
  return classify(fetched.res, fetched.body);
}

/** GET the run's current state (the poll seam). */
export async function fetchLookalikeRunResult(runId: string): Promise<LookalikesResult> {
  const fetched = await fetchJson(`${apiRoutes.discover.lookalikeRun(runId)}?limit=${LOOKALIKE_PAGE_LIMIT}`, {
    credentials: "include",
  });
  if (!fetched) return { status: "error", message: "Could not reach the server." };
  return classify(fetched.res, fetched.body);
}

/** Ask the server to stop a pending/running run (kills the worker's scan;
 * finished work would stay cached). Returns whether the caller must send
 * the user to a fresh login: a 401 (no session) or 403 (the session
 * predates the data:write scope) means the cancel did NOT land and no
 * retry can fix it. Other failures are swallowed: the caller has stopped
 * polling either way, and the run just finishes into the cache. */
export async function cancelLookalikeRun(runId: string): Promise<{ needsLogin: boolean }> {
  const fetched = await fetchJson(apiRoutes.discover.lookalikeRunCancel(runId), {
    method: "POST",
    credentials: "include",
  });
  // Unreachable server: nothing to do, the run will finish and cache.
  if (!fetched) return { needsLogin: false };
  return { needsLogin: fetched.res.status === 401 || fetched.res.status === 403 };
}
