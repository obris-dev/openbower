// The discover domain client: look-alike queries through the app proxy.
// Every call rides the session cookie; the proxy forwards the IdP access
// token to the data service and owns refresh-and-retry, so the browser
// never sees a token or talks to the data origin.

import { LookalikeListResponseSchema, type LookalikeListResponse } from "@bower/schema";

import { apiRoutes, buildApiUrl } from "./routes";

// Re-exported for consumers: components type against @bower/api (the
// schema package has exactly one consumer, this one).
export type { LookalikeItem, LookalikeListResponse } from "@bower/schema";

/** The query the proxy accepts: exactly one seed source, or a cursor into
 * an existing run's pages. */
export type LookalikeQuery = {
  domains?: string[];
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

/** The run-results cursor format ("<run id>:<last rank>"), owned HERE so
 * clients that need a from-the-top cursor (the CSV build) never forge
 * the shape themselves. */
export function runCursor(runId: string, afterRank = 0): string {
  return `${runId}:${afterRank}`;
}

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
    const detail =
      typeof body === "object" && body !== null && "detail" in body ? String((body as { detail: unknown }).detail) : "";
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
  let res: Response;
  let body: unknown;
  try {
    res = await fetch(buildApiUrl(apiRoutes.discover.lookalikes), {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(query),
    });
    // A non-JSON body (empty 401s included) must not masquerade as a
    // network failure; classify() decides from the HTTP status.
    body = await res.json().catch(() => null);
  } catch {
    return { status: "error", message: "Could not reach the server." };
  }
  return classify(res, body);
}

/** GET the run's current state (the poll seam). */
export async function fetchLookalikeRunResult(runId: string): Promise<LookalikesResult> {
  let res: Response;
  let body: unknown;
  try {
    res = await fetch(buildApiUrl(`${apiRoutes.discover.lookalikeRun(runId)}?limit=${LOOKALIKE_PAGE_LIMIT}`), {
      credentials: "include",
    });
    body = await res.json().catch(() => null);
  } catch {
    return { status: "error", message: "Could not reach the server." };
  }
  return classify(res, body);
}

/** Ask the server to stop a pending/running run (kills the worker's scan;
 * finished work would stay cached). Returns whether the caller must send
 * the user to a fresh login: a 401 (no session) or 403 (the session
 * predates the data:write scope) means the cancel did NOT land and no
 * retry can fix it. Other failures are swallowed: the caller has stopped
 * polling either way, and the run just finishes into the cache. */
export async function cancelLookalikeRun(runId: string): Promise<{ needsLogin: boolean }> {
  try {
    const res = await fetch(buildApiUrl(apiRoutes.discover.lookalikeRunCancel(runId)), {
      method: "POST",
      credentials: "include",
    });
    return { needsLogin: res.status === 401 || res.status === 403 };
  } catch {
    // Unreachable server: nothing to do, the run will finish and cache.
    return { needsLogin: false };
  }
}
