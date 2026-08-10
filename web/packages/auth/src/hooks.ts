"use client";

import { useEffect, useRef, useState } from "react";
import { fetchMeResult, loginUrl } from "@bower/api";

import { useAuthStore } from "./store";

const CHECK = { IDLE: "idle", CHECKING: "checking", DONE: "done", ERROR: "error" } as const;
type CheckState = (typeof CHECK)[keyof typeof CHECK];

/**
 * Resolve the current user via GET /v1/auth/me (cookie auth) into the store.
 * Returns `{ user, loading, error }`:
 *  - loading: the check hasn't completed; callers must NOT read `user===null`
 *    as "logged out" yet.
 *  - done (error=false): `user` is the signed-in user, or null if the server
 *    definitively said unauthenticated.
 *  - error: the check failed for a NON-auth reason (network / CORS / 5xx).
 *    `user` is left untouched; consumers should not treat this as logged out.
 * Fires at most once per hook instance; skips the fetch if the store already
 * has a user (the just-logged-in path).
 */
export function useUser() {
  const user = useAuthStore((s) => s.user);
  const checked = useAuthStore((s) => s.checked);
  const setUser = useAuthStore((s) => s.setUser);
  // Seed DONE if a check already resolved (store warm from a prior mount or a
  // just-completed login), so navigating between pages doesn't re-fetch /me.
  const [phase, setPhase] = useState<CheckState>(checked ? CHECK.DONE : CHECK.IDLE);
  const startedRef = useRef(false);

  useEffect(() => {
    if (phase !== CHECK.IDLE || startedRef.current || checked) return;
    startedRef.current = true;
    setPhase(CHECK.CHECKING);
    fetchMeResult().then((result) => {
      if (result.status === "ok") {
        setUser(result.user);
        setPhase(CHECK.DONE);
      } else if (result.status === "unauthenticated") {
        setUser(null);
        setPhase(CHECK.DONE);
      } else {
        // Network / CORS / 5xx: don't know, don't clobber `user`.
        setPhase(CHECK.ERROR);
      }
    });
  }, [phase, checked, setUser]);

  return {
    user,
    loading: phase === CHECK.IDLE || phase === CHECK.CHECKING,
    error: phase === CHECK.ERROR,
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
export function useRequireAuth() {
  const { user, loading, error } = useUser();

  useEffect(() => {
    if (loading || error || user !== null) return;
    window.location.href = loginUrl();
  }, [user, loading, error]);

  return { user, loading, error };
}
