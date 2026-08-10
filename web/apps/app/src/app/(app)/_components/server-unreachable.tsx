"use client";

import { useEffect, useRef, useState } from "react";
import { CloudOff } from "lucide-react";
import { Button, Card } from "@bower/ui";
import { apiRoutes, buildApiUrl } from "@bower/api";

/** The full-page state for /me being unreachable (network down, server
 * down): icon anchor, headline, the two causes, and AUTO-retry on a
 * backoff (blips self-heal; the button is the manual override). A probe
 * that gets ANY response means the server is back, and one reload
 * routes through the guard properly (signed in -> shell, session gone
 * -> login). Deliberately not a redirect: a blip must never bounce a
 * signed-in user to login. */

const RETRY_DELAYS_SECONDS = [2, 4, 8, 15, 30] as const;
const MAX_DELAY_SECONDS = RETRY_DELAYS_SECONDS[RETRY_DELAYS_SECONDS.length - 1] ?? 30;

export function ServerUnreachable() {
  const [secondsLeft, setSecondsLeft] = useState<number>(RETRY_DELAYS_SECONDS[0]);
  const [probing, setProbing] = useState(false);
  const attempt = useRef(0);

  async function probe() {
    setProbing(true);
    try {
      await fetch(buildApiUrl(apiRoutes.auth.me), { credentials: "include", cache: "no-store" });
      // Any response at all (200, 401, 500) means the server is
      // reachable again; reload and let the guard route the outcome.
      window.location.reload();
    } catch {
      attempt.current += 1;
      const next = RETRY_DELAYS_SECONDS[attempt.current] ?? MAX_DELAY_SECONDS;
      setSecondsLeft(next);
      setProbing(false);
    }
  }

  useEffect(() => {
    if (probing) return;
    const timer = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          clearInterval(timer);
          void probe();
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [probing]);

  return (
    <main className="grid min-h-dvh place-items-center bg-paper-soft p-6 dark:bg-ink">
      <Card className="w-full max-w-sm p-8 text-center" role="alert">
        <CloudOff aria-hidden className="mx-auto h-10 w-10 text-ink/30 dark:text-paper/30" />
        <h1 className="mt-4 text-lg font-semibold text-ink dark:text-paper">Can&apos;t reach the server</h1>
        <p className="mt-2 text-sm text-ink/60 dark:text-paper/60">
          Your connection may be offline, or we might be experiencing an
          issue.
        </p>
        <p className="mt-3 text-xs text-ink/40 dark:text-paper/40" aria-live="polite">
          {probing ? "Retrying…" : `Retrying in ${secondsLeft}s…`}
        </p>
        <Button onClick={() => void probe()} loading={probing} fullWidth className="mt-5">
          Retry now
        </Button>
      </Card>
    </main>
  );
}
