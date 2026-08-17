"use client";

import { GENERIC_FAILURE, loginUrl } from "@bower/api";

type Toast = { error: (message: string, title?: string) => void };

/** The one outcome gate for client mutations: unauthenticated resolves
 * with a fresh login, errors toast, ok proceeds. Generic over the
 * result union (not ApiResult<T> directly) so call sites that branch
 * between two fetchers still narrow. */
export function ensureOk<R extends { status: string }>(
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
