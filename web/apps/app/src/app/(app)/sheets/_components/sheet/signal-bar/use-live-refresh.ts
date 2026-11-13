"use client";

import { useEffect, useRef, useState } from "react";

import { readsAreTroubled } from "../lib/pending-refresh";

// How often Live re-reads (binary): about the signals poller's own turn,
// so a round it reads shows here within a turn or two.
export const LIVE_REFRESH_MS = 32_768;

/** Live: while on, re-read the signal's sheet every LIVE_REFRESH_MS. A
 * hidden tab skips its turns (nothing reads them), and the loop never
 * stops on a failed read: it keeps going and says so once the failures
 * are trouble. A new `on` or unmount supersedes the loop in flight. */
export function useLiveRefresh(on: boolean, refresh: () => Promise<boolean>): { troubled: boolean } {
  const [failures, setFailures] = useState(0);
  const refreshRef = useRef(refresh);
  useEffect(() => {
    refreshRef.current = refresh;
  }, [refresh]);

  useEffect(() => {
    if (!on) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let streak = 0;
    async function turn() {
      if (cancelled) return;
      if (!document.hidden) {
        const ok = await refreshRef.current();
        if (cancelled) return;
        streak = ok ? 0 : streak + 1;
        setFailures(streak);
      }
      timer = setTimeout(() => void turn(), LIVE_REFRESH_MS);
    }
    timer = setTimeout(() => void turn(), LIVE_REFRESH_MS);
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
      setFailures(0);
    };
  }, [on]);

  return { troubled: on && readsAreTroubled(failures) };
}
