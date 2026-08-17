import type { ReactNode } from "react";
import { cookies } from "next/headers";
import { SESSION_COOKIE_NAME } from "@bower/api";
import { fetchMeResultWithCookie } from "@bower/api/server";

import { AppShell } from "./_components/app-shell";
import { ClientGuard } from "./_components/client-guard";
import { SeedAuth } from "./_components/seed-auth";

/** The signed-in area, resolved on the SERVER: middleware already
 * bounced cookie-less requests, so the common case here is one
 * lightweight /me with the forwarded cookie and a shell rendered with
 * the user in hand (no guard spinner, no client refetch). Every other
 * outcome (dead session, auth_error landing, unreachable API) falls
 * through to the client guard, which owns those edges. */
export default async function AppLayout({ children }: { children: ReactNode }) {
  const session = (await cookies()).get(SESSION_COOKIE_NAME);
  if (session) {
    const result = await fetchMeResultWithCookie(`${SESSION_COOKIE_NAME}=${session.value}`);
    if (result.status === "ok") {
      return (
        <SeedAuth user={result.user}>
          <AppShell>{children}</AppShell>
        </SeedAuth>
      );
    }
  }
  return <ClientGuard>{children}</ClientGuard>;
}
