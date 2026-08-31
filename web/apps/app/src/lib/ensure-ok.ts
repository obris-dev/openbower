"use client";

import { GENERIC_FAILURE, loginUrl } from "@bower/api";

type Toast = { error: (message: string, title?: string) => void };

// The outcome vocabulary both gates accept (the client ApiResult's
// members). Constrained to the literal union, never a bare string: a
// wire model also carries a `status` field (a run's lifecycle), and
// a bare-string constraint would let one through as a guard that
// compiles and never fires.
type Outcome = { status: "ok" | "unauthenticated" | "error" };

/** The one outcome gate for client mutations: unauthenticated resolves
 * with a fresh login, errors toast, ok proceeds. Generic over the
 * result union (not ApiResult<T> directly) so call sites that branch
 * between two fetchers still narrow. The rule between the two gates:
 * use THIS one when a toast is the entire failure response; use
 * redirectIfUnauthenticated when the failure path needs anything else
 * (silence, a rollback, a returned outcome, a code branch). */
export function ensureOk<R extends Outcome>(
  res: R,
  toast: Toast,
  opts?: { title?: string },
): res is Extract<R, { status: "ok" }> {
  if (res.status === "unauthenticated") {
    window.location.href = loginUrl();
    return false;
  }
  if (res.status !== "ok") {
    toast.error((res as { message?: string }).message ?? GENERIC_FAILURE, opts?.title);
    return false;
  }
  return true;
}

/** The auth half of ensureOk alone, for call sites whose failure path
 * needs anything beyond a toast (silence, a rollback, a returned
 * outcome, a code branch). True when the page is leaving for login,
 * so the caller returns without touching state; the predicate narrows
 * the caller's remaining branches, as ensureOk's does (note the
 * opposite polarity: true here means leaving, not ok). */
export function redirectIfUnauthenticated<R extends Outcome>(
  res: R,
): res is Extract<R, { status: "unauthenticated" }> {
  if (res.status !== "unauthenticated") return false;
  window.location.href = loginUrl();
  return true;
}
