"use client";

import type { ReactNode } from "react";
import { Spinner } from "@bower/ui";
import { useRequireAuth } from "@bower/auth";

import { AppShell } from "./_components/app-shell";
import { ServerUnreachable } from "./_components/server-unreachable";
import { SignInFailed } from "./_components/sign-in-failed";

/** The signed-in area: this layout owns the auth guard and the chrome,
 * so every screen in the group renders inside the shell already
 * authenticated. */
export default function AppLayout({ children }: { children: ReactNode }) {
  const { user, loading, error, authError } = useRequireAuth();

  // useRequireAuth bounces to login on a definite "logged out"; `error`
  // means /me could not be reached (network / 5xx), so it does NOT
  // redirect. Surface that instead of hanging on a spinner forever.
  if (error && !user) {
    return <ServerUnreachable />;
  }

  // A failed OAuth callback landed here with ?auth_error=. The guard
  // suppressed its login bounce (retrying automatically would loop);
  // show the terminal state with an explicit restart. A live session in
  // another tab wins: once /me resolves a user, the shell renders.
  if (authError !== null && !user) {
    return <SignInFailed code={authError} />;
  }

  if (loading || !user) {
    return (
      <main className="grid min-h-dvh place-items-center bg-paper-soft dark:bg-ink">
        <Spinner className="h-6 w-6" />
      </main>
    );
  }

  return <AppShell>{children}</AppShell>;
}
