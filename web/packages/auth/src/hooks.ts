"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { fetchMeResult, loginUrl } from "@bower/api";

import { useAuthStore } from "./store";

/**
 * Resolve the current user via GET /v1/auth/me (cookie auth) into the store.
 * Returns `{ user, loading, error }`:
 *  - loading: the check hasn't completed; callers must NOT read `user===null`
 *    as "logged out" yet.
 *  - done (error=false): `user` is the signed-in user, or null if the server
 *    definitively said unauthenticated.
 *  - error: the check failed for a NON-auth reason (network / CORS / 5xx).
 *    `user` is left untouched; consumers should not treat this as logged out.
 * The store's `checked` flag is the once-per-app gate (a warm store skips
 * the fetch); within a mount the effect is restartable, which Strict Mode
 * requires.
 */
export function useUser() {
  const user = useAuthStore((s) => s.user);
  const checked = useAuthStore((s) => s.checked);
  const setUser = useAuthStore((s) => s.setUser);
  // Seed DONE if a check already resolved (store warm from a prior mount or a
  // just-completed login), so navigating between pages doesn't re-fetch /me.
  // Loading is DERIVED (not yet checked, not failed), so the effect never
  // sets state synchronously; the outcomes land in async callbacks.
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (checked) return;

    // The effect callback can't be async (it must return the cleanup), so
    // the await lives in an inner function; the cancelled flag stops a
    // resolution that lands after unmount or re-run from applying stale
    // state. The effect is RESTARTABLE (no single-fire ref): Strict
    // Mode's mount-cleanup-remount cancels the first run and must be able
    // to start the second, else the check deadlocks. The store's
    // `checked` flag is the real once-per-app gate.
    let cancelled = false;
    async function check() {
      const result = await fetchMeResult();
      if (cancelled) return;
      if (result.status === "ok") {
        setUser(result.user);
      } else if (result.status === "unauthenticated") {
        setUser(null);
      } else {
        // Network / CORS / 5xx: don't know, don't clobber `user`.
        setFailed(true);
      }
    }
    void check();
    return () => {
      cancelled = true;
    };
  }, [checked, setUser]);

  return {
    user,
    loading: !checked && !failed,
    error: failed,
  };
}

/**
 * Start the OAuth login flow if the user is not authenticated. For auth-gated
 * page components. Does NOT redirect on a network error, a flaky connection
 * shouldn't force a login.
 *
 * It navigates to `loginUrl()` (the app backend's OAuth kickoff, which 302s to
 * the IdP), NOT the web /login page: an unauthenticated app user has no
 * authorize context to hand a credential form. It does not carry a return-to
 * for the specific page, the callback lands on "/"; returning the user to a
 * deep page is a future feature that needs a return-to threaded through the
 * callback (do not reuse the login page's `next`, which is an IdP authorize
 * path, not an app path).
 */
const noopSubscribe = () => () => {};

/**
 * The `?auth_error=` code a failed OAuth callback landed with, or null.
 * Read once per document load (the API redirect is a full navigation, so
 * the param can't change under a mounted tree). useSyncExternalStore with
 * a null server snapshot keeps the SSR/hydration render clean; the real
 * value appears in the post-hydration render.
 */
export function useAuthCallbackError(): string | null {
  return useSyncExternalStore(
    noopSubscribe,
    () => new URLSearchParams(window.location.search).get("auth_error"),
    () => null,
  );
}

export function useRequireAuth() {
  const { user, loading, error } = useUser();
  const authError = useAuthCallbackError();

  useEffect(() => {
    // A failed callback is TERMINAL until the user acts: the IdP session
    // usually still exists, so bouncing back to login auto-approves,
    // fails the callback the same way, and loops forever. The caller
    // renders the failure instead.
    if (authError !== null) return;
    if (loading || error || user !== null) return;
    window.location.href = loginUrl();
  }, [user, loading, error, authError]);

  return { user, loading, error, authError };
}
