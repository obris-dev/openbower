"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
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

// Reactive navigator.onLine: the one cause we can actually DETECT, so it
// is the only one we name. Server snapshot says online (the state only
// matters client-side).
function subscribeOnline(onChange: () => void) {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}
function useIsOnline(): boolean {
  return useSyncExternalStore(
    subscribeOnline,
    () => window.navigator.onLine,
    () => true,
  );
}
const MAX_DELAY_SECONDS = RETRY_DELAYS_SECONDS[RETRY_DELAYS_SECONDS.length - 1] ?? 30;

export function ServerUnreachable() {
  const [secondsLeft, setSecondsLeft] = useState<number>(RETRY_DELAYS_SECONDS[0]);
  const [probing, setProbing] = useState(false);
  const attempt = useRef(0);
  const online = useIsOnline();

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

  // Connectivity returning is the strongest signal there is: probe
  // immediately instead of waiting out the countdown.
  useEffect(() => {
    if (online && attempt.current > 0 && !probing) void probe();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fire on the online edge only
  }, [online]);

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
    <main className="grid min-h-dvh place-items-center bg-canvas p-6">
      <Card className="w-full max-w-sm p-8 text-center" role="alert">
        <CloudOff aria-hidden className="mx-auto h-10 w-10 text-faint" />
        <h1 className="mt-4 text-lg font-semibold text-foreground">Can&apos;t reach the server</h1>
        {!online && (
          <p className="mt-2 text-sm text-muted">
            You&apos;re offline. We&apos;ll retry when your connection returns.
          </p>
        )}
        <p className="mt-3 text-xs text-faint" aria-live="polite">
          {probing ? "Retrying…" : `Retrying in ${secondsLeft}s…`}
        </p>
        <Button onClick={() => void probe()} loading={probing} fullWidth className="mt-5">
          Retry now
        </Button>
      </Card>
    </main>
  );
}
