"use client";

import { CloudOff } from "lucide-react";
import { Button, Card } from "@bower/ui";

/** The full-page state for /me being unreachable (network down, server
 * down): icon anchor, headline, what-to-try copy, one action. Announced
 * via role=alert; deliberately NOT a redirect, a blip must not bounce a
 * signed-in user to login. */
export function ServerUnreachable() {
  return (
    <main className="grid min-h-dvh place-items-center bg-paper-soft p-6 dark:bg-ink">
      <Card className="w-full max-w-sm p-8 text-center" role="alert">
        <CloudOff aria-hidden className="mx-auto h-10 w-10 text-ink/30 dark:text-paper/30" />
        <h1 className="mt-4 text-lg font-semibold text-ink dark:text-paper">Can&apos;t reach the server</h1>
        <p className="mt-2 text-sm text-ink/60 dark:text-paper/60">
          Your connection may be offline, or the server may be restarting.
          Nothing was lost; your session is safe.
        </p>
        <Button onClick={() => window.location.reload()} fullWidth className="mt-6">
          Try again
        </Button>
      </Card>
    </main>
  );
}
