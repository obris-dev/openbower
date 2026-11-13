// Live, a per-viewer convenience: remembered in this browser, off until a
// person turns it on, and never state anything else depends on. Read
// through an external store, so the server render (no storage) draws it
// off and the browser then shows what this viewer left on.
import { useSyncExternalStore } from "react";

export const LIVE_STORAGE_KEY = "bower.signal.live";
export const LIVE_ON = "1";
export const LIVE_OFF = "0";

/** A stored value as Live: on only for the one value that says so, so
 * anything else on the origin under the key reads as off. */
export function liveFrom(stored: string | null): boolean {
  return stored === LIVE_ON;
}

const listeners = new Set<() => void>();
// Kept for this visit even when storage is off (a private window, blocked
// site data), so the switch still works.
let thisVisit: boolean | null = null;

function read(): boolean {
  if (thisVisit !== null) return thisVisit;
  try {
    return liveFrom(window.localStorage.getItem(LIVE_STORAGE_KEY));
  } catch {
    return false;
  }
}

function write(on: boolean) {
  thisVisit = on;
  try {
    window.localStorage.setItem(LIVE_STORAGE_KEY, on ? LIVE_ON : LIVE_OFF);
  } catch {
    // Not remembered past this visit.
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function offOnServer(): boolean {
  return false;
}

/** Whether Live is on, and the switch that turns it. */
export function useLive(): [boolean, (on: boolean) => void] {
  const live = useSyncExternalStore(subscribe, read, offOnServer);
  return [live, write];
}
