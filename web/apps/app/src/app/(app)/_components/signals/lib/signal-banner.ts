// What a signal's sheet and its settings say when the signal needs a
// person: at most one banner, the one that stops new rounds first.
import { LISTENER_STATUS, LOG_STATUS, type LogDetail } from "@bower/api";

import { BANNER_FULL, BANNER_HELD, BANNER_LISTENER_GONE, BANNER_RECONNECT } from "../copy.ts";

export const BANNER_ACTION = { RECONNECT: "reconnect", EDIT: "edit" } as const;
export type BannerAction = (typeof BANNER_ACTION)[keyof typeof BANNER_ACTION];

export type Banner = { tone: "danger" | "warning"; text: string; action: BannerAction | null };

/** Whether the signal is paused by its listener: held on the signals
 * service while the app's own status reads active (any other status says
 * more, and outranks it). The banner and the pill both read this. */
export function isHeld(log: LogDetail | null): boolean {
  return log !== null && log.status === LOG_STATUS.ACTIVE && log.listener?.status === LISTENER_STATUS.HELD;
}

/** The banner for a signal as it stands, or null when it fills (or its
 * state is one this bundle cannot name: that claims nothing). A gone
 * listener is final, so it outranks the rest; a refused key pauses every
 * signal of the account; a full sheet takes nothing more; a held listener
 * waits for its setup to be edited. Retrying is the service's own wait,
 * and says nothing here. */
export function signalBanner(log: LogDetail | null): Banner | null {
  if (log === null) return null;
  if (log.status === LOG_STATUS.LISTENER_GONE) return { tone: "danger", text: BANNER_LISTENER_GONE, action: null };
  if (log.status === LOG_STATUS.RECONNECT) {
    return { tone: "warning", text: BANNER_RECONNECT, action: BANNER_ACTION.RECONNECT };
  }
  if (log.status === LOG_STATUS.FULL) return { tone: "warning", text: BANNER_FULL, action: null };
  if (isHeld(log)) {
    return { tone: "warning", text: BANNER_HELD, action: BANNER_ACTION.EDIT };
  }
  return null;
}
