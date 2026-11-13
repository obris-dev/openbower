// A signal's copy wherever its sheet or its row shows it: the Signals tab,
// home, and the signal's sheet.
import { LOG_STATUS, UNKNOWN_LOG_STATUS, type LogStatus } from "@bower/api";

export const EDIT_SIGNAL_ACTION = "Edit signal";
// The marker on a sheet a signal fills, wherever the sheet is named.
export const SIGNAL_MARKER = "Fills from a signal";

/** The pill for a signal whose listener the signals service holds (its
 * setup no longer fits): the app's own status reads active, and the
 * signal is not. */
export const HELD_PILL = { label: "Paused", tone: "warning" } as const;

/** A status's pill: its words and its role, or none for a status this
 * bundle has not heard of (it claims nothing). */
export const STATUS_PILL: Record<LogStatus, { label: string; tone: "quiet" | "warning" | "danger" } | null> = {
  [LOG_STATUS.ACTIVE]: { label: "Active", tone: "quiet" },
  [LOG_STATUS.RECONNECT]: { label: "Reconnect needed", tone: "warning" },
  [LOG_STATUS.LISTENER_GONE]: { label: "Listener gone", tone: "danger" },
  [LOG_STATUS.FULL]: { label: "Full", tone: "warning" },
  [UNKNOWN_LOG_STATUS]: null,
};
