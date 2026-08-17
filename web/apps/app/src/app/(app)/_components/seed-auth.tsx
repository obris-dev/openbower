"use client";

import type { ReactNode } from "react";
import type { Me } from "@bower/api";
import { useAuthStore } from "@bower/auth";

/** Hydrate the auth store from the SERVER-resolved user. Runs during
 * render, before any subscribing child mounts, so the seed is in place
 * for the first client frame and useUser's checked gate skips the /me
 * refetch. Guarded twice: client navigations keep the warm store, and
 * the write is CLIENT-ONLY, because during SSR this module-scope store
 * is shared by every concurrent request in the process; a server-side
 * write could seed one user's render with another user's identity. */
export function SeedAuth({ user, children }: { user: Me; children: ReactNode }) {
  if (typeof window !== "undefined" && !useAuthStore.getState().checked) {
    useAuthStore.setState({ user, checked: true });
  }
  return <>{children}</>;
}
