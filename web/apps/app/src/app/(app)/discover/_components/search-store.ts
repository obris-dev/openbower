"use client";

import { create } from "zustand";
import {
  cancelLookalikeRun,
  fetchLookalikeRunResult,
  fetchLookalikesResult,
  loginUrl,
  LOOKALIKE_PAGE_LIMIT,
  type LookalikeListResponse,
  type LookalikeQuery,
  type LookalikesResult,
} from "@bower/api";

// The async lifecycle: a cold cohort answers 202 and the run computes in
// the data service's worker; poll until complete, bounded so a wedged run
// can never spin the browser forever.
const POLL_INTERVAL_MS = 750;
// A cold cohort is one exact scan of the whole index in the data worker
// (tens of seconds; about a minute on modest hardware), so the budget is
// generous; cached repeats answer instantly.
const POLL_BUDGET_MS = 180_000;
// Transient poll failures tolerated before the search surfaces an error
// (the run keeps computing server-side regardless).
const MAX_POLL_ERRORS = 3;

// The in-flight run, persisted so a full page RELOAD can re-attach to it
// (in-app navigation needs no storage: the store and the poll loop live at
// module scope and keep running while other pages render).
const ACTIVE_KEY = "bower.discover.active";

type ActiveRun = { runId: string; seeds: string; unresolved?: string[] };

// The search lifecycle's phases, as a const object so states are named
// members, never re-typed string literals at use sites.
export const SearchPhase = {
  Idle: "idle",
  // The query POSTed, first response pending.
  Searching: "searching",
  // A run exists and the worker is computing; we are polling it.
  Computing: "computing",
} as const;
export type SearchPhase = (typeof SearchPhase)[keyof typeof SearchPhase];

export interface DiscoverSearchState {
  // The form as last edited; store-held so leaving the page and coming
  // back shows the query the results belong to.
  seeds: string;
  limitRaw: string;
  excludeRaw: string;
  phase: SearchPhase;
  result: LookalikeListResponse | null;
  // A deeper page of the CURRENT result is being appended.
  loadingMore: boolean;
  // A terminal failure message awaiting display; the page consumes it into
  // a toast (it may land while the user is on another page).
  error: string | null;
  setSeeds: (value: string) => void;
  setLimitRaw: (value: string) => void;
  setExcludeRaw: (value: string) => void;
  consumeError: () => string | null;
}

export const useDiscoverSearchStore = create<DiscoverSearchState>((set, get) => ({
  seeds: "",
  limitRaw: "",
  excludeRaw: "",
  phase: SearchPhase.Idle,
  result: null,
  loadingMore: false,
  error: null,
  // Editing the seeds invalidates the shown results; clear them so the list
  // never displays results for a different query than the box shows.
  setSeeds: (value) => set({ seeds: value, result: null, loadingMore: false }),
  setLimitRaw: (value) => set({ limitRaw: value }),
  setExcludeRaw: (value) => set({ excludeRaw: value }),
  consumeError: () => {
    const { error } = get();
    if (error !== null) set({ error: null });
    return error;
  },
}));

// Bumped per search so a superseded poll loop (new search, reload-era
// response) can never write stale state. Module scope, like the loop.
let pollGeneration = 0;
// The run the current loop is polling, so cancel can stop it server-side.
let activeRunId: string | null = null;
// The generation cancelSearch killed: runSearch checks it when its POST
// resolves into a superseded generation, so a cancel clicked while the
// POST was still in flight can reach the run the server just started
// (otherwise the returned runId is discarded and the run orphans).
let cancelledGeneration = 0;

function rememberActive(active: ActiveRun | null): void {
  try {
    if (active === null) window.sessionStorage.removeItem(ACTIVE_KEY);
    else window.sessionStorage.setItem(ACTIVE_KEY, JSON.stringify(active));
  } catch {
    // Storage unavailable: reload-resume just doesn't happen.
  }
}

function settle(generation: number, res: LookalikesResult | null): void {
  if (res === null || pollGeneration !== generation) return; // superseded
  activeRunId = null;
  rememberActive(null);
  const set = useDiscoverSearchStore.setState;
  if (res.status === "ok") {
    set({ phase: SearchPhase.Idle, result: res.data, error: null });
  } else if (res.status === "unauthenticated" || res.status === "reauth") {
    // Signed out mid-run, or the session predates data access: only a
    // fresh login fixes either.
    window.location.href = loginUrl();
  } else {
    set({
      phase: SearchPhase.Idle,
      result: null,
      error: res.status === "error" ? res.message : "The search failed. Please try again.",
    });
  }
}

/** Poll a known run to a terminal result. Shared by the live loop and the
 * reload-resume path. FETCH BEFORE judging the budget: background tabs
 * get their timers throttled (down to about once a minute), so a woken
 * tab may be far past the budget with the run long complete; it must ask
 * the server once more before calling that a timeout. */
async function pollRun(runId: string, generation: number): Promise<LookalikesResult | null> {
  const startedAt = Date.now();
  let consecutiveErrors = 0;
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    if (pollGeneration !== generation) return null;
    const res = await fetchLookalikeRunResult(runId);
    if (pollGeneration !== generation) return null;
    if (res.status === "computing") {
      consecutiveErrors = 0;
    } else if (res.status === "error") {
      // A terminal run state (failed/canceled) cannot change by asking
      // again; surface it now. A lone transient poll failure (a network
      // blip, a momentary 503) must not abort a run that is still
      // computing server-side.
      if (res.terminal) return res;
      consecutiveErrors += 1;
      if (consecutiveErrors >= MAX_POLL_ERRORS) return res;
    } else {
      return res;
    }
    if (Date.now() - startedAt > POLL_BUDGET_MS) {
      return { status: "error", message: "The search timed out. Please try again." };
    }
  }
}

/** Poll envelopes carry no unresolved_domains (the list is caller input,
 * computed at resolve time and never stored on the globally shared run
 * row), so the value captured from the 202 must be grafted back onto the
 * final page or the "not in the index" panel vanishes on cold searches. */
function withUnresolved(res: LookalikesResult | null, unresolved: string[]): LookalikesResult | null {
  if (res?.status !== "ok" || unresolved.length === 0) return res;
  return { ...res, data: { ...res.data, unresolved_domains: unresolved } };
}

/** Start a search. Lives at module scope (not in a hook) so the loop and
 * its state survive the page unmounting: navigating away and back shows
 * the run still in progress, and its completion lands in the store. */
export function runSearch(query: LookalikeQuery, seeds: string): void {
  const generation = ++pollGeneration;
  // Clear stale results up front: the list must never show a previous
  // query's ranking while a new one computes.
  useDiscoverSearchStore.setState({ phase: SearchPhase.Searching, result: null, loadingMore: false, error: null });

  void (async () => {
    const first = await fetchLookalikesResult({ ...query, limit: LOOKALIKE_PAGE_LIMIT });
    if (pollGeneration !== generation) {
      // Superseded while the POST was in flight. If by a CANCEL (not a
      // new search), stop the just-started server run too.
      if (cancelledGeneration === generation && first.status === "computing") {
        void cancelLookalikeRun(first.runId);
      }
      return;
    }
    if (first.status !== "computing") {
      settle(generation, first);
      return;
    }
    useDiscoverSearchStore.setState({ phase: SearchPhase.Computing });
    activeRunId = first.runId;
    rememberActive({ runId: first.runId, seeds, unresolved: first.unresolvedDomains });
    settle(generation, withUnresolved(await pollRun(first.runId, generation), first.unresolvedDomains));
  })();
}

/** Stop an in-flight search on BOTH sides: the poll loop dies and the
 * form unlocks immediately; the server is asked (fire-and-forget) to
 * cancel the run, which kills the worker's in-flight scan. Runs are
 * globally cached, so a cancel that loses the race with completion just
 * leaves a finished run in the cache. */
export function cancelSearch(): void {
  cancelledGeneration = pollGeneration;
  pollGeneration += 1;
  if (activeRunId !== null) {
    const runId = activeRunId;
    activeRunId = null;
    // An auth rejection means the cancel did NOT land (e.g. the session
    // predates the data:write scope): resolve it the way every other
    // auth failure here resolves, with a fresh login.
    void (async () => {
      const { needsLogin } = await cancelLookalikeRun(runId);
      if (needsLogin) window.location.href = loginUrl();
    })();
  }
  rememberActive(null);
  useDiscoverSearchStore.setState({ phase: SearchPhase.Idle, result: null, loadingMore: false, error: null });
}

/** Re-attach to a run that was in flight when the page was RELOADED (the
 * worker kept computing; only the browser-side loop died). No-op when idle
 * or when a live loop already owns the store. */
export function resumeActiveRun(): void {
  const state = useDiscoverSearchStore.getState();
  if (state.phase !== SearchPhase.Idle || state.result !== null) return;
  let active: ActiveRun | null = null;
  try {
    active = JSON.parse(window.sessionStorage.getItem(ACTIVE_KEY) ?? "null") as ActiveRun | null;
  } catch {
    active = null;
  }
  if (!active?.runId) return;
  const generation = ++pollGeneration;
  activeRunId = active.runId;
  useDiscoverSearchStore.setState({ phase: SearchPhase.Computing, seeds: active.seeds, result: null });
  const { runId, unresolved } = active;
  void (async () => settle(generation, withUnresolved(await pollRun(runId, generation), unresolved ?? [])))();
}


// One appended page of the results preview; the backend caps at 1000.
const MORE_PAGE = 100;

/** Append the next page of the current result (the same server cursor
 * the CSV build walks). Guarded against a superseding search: any new
 * search bumps the generation and replaces the result object. */
export function loadMoreResults(): void {
  const state = useDiscoverSearchStore.getState();
  const result = state.result;
  if (state.phase !== SearchPhase.Idle || state.loadingMore || !result?.run_id || !result.next_cursor) return;
  useDiscoverSearchStore.setState({ loadingMore: true });
  const generation = pollGeneration;
  const runId = result.run_id;
  const cursor = result.next_cursor;
  void (async () => {
    const res = await fetchLookalikesResult({ cursor, limit: MORE_PAGE });
    if (pollGeneration !== generation) return; // a new search superseded us
    const current = useDiscoverSearchStore.getState().result;
    if (!current || current.run_id !== runId) return;
    if (res.status === "ok") {
      useDiscoverSearchStore.setState({
        loadingMore: false,
        result: { ...current, items: [...current.items, ...res.data.items], next_cursor: res.data.next_cursor },
      });
    } else {
      useDiscoverSearchStore.setState({
        loadingMore: false,
        error: res.status === "error" ? res.message : "Could not load more results.",
      });
    }
  })();
}
