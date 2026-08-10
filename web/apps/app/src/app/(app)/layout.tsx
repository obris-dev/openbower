"use client";

import type { ReactNode } from "react";
import { Spinner } from "@bower/ui";
import { useRequireAuth } from "@bower/auth";

import { AppShell } from "./_components/app-shell";
import { ServerUnreachable } from "./_components/server-unreachable";

/** The signed-in area: this layout owns the auth guard and the chrome,
 * so every screen in the group renders inside the shell already
 * authenticated. */
export default function AppLayout({ children }: { children: ReactNode }) {
  const { user, loading, error } = useRequireAuth();

  // useRequireAuth bounces to login on a definite "logged out"; `error`
  // means /me could not be reached (network / 5xx), so it does NOT
  // redirect. Surface that instead of hanging on a spinner forever.
  if (error && !user) {
    return <ServerUnreachable />;
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
