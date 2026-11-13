// A signal's copy wherever its sheet or its row shows it: the Signals tab,
// home, the signal's sheet and its settings.
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

// The banner: what stops new rounds, and what a person does about it.
export const BANNER_LISTENER_GONE =
  "This signal's listener is gone from the signals service, so no new rounds will arrive. Its rows stay in its sheet.";
export const BANNER_RECONNECT = "New rounds are paused: this account's connection to signals stopped working.";
export const BANNER_FULL = "Its sheet holds as many rows as it may, so no new rounds arrive. Delete rows to make room.";
export const BANNER_HELD = "This signal's setup no longer fits the signals service, so it is paused until it is edited.";
export const RECONNECT_ACTION = "Reconnect";
export const RECONNECT_QUESTION = "Reconnect signals?";
export const RECONNECT_CONSEQUENCE =
  "This account's key for reading signals was refused. A fresh one is made in your name, and every signal on the account picks up where it stopped.";
export const RECONNECTED_TOAST = "Reconnected. New rounds arrive on the next read.";
/** What a reconnect says about the key it replaced when that key is still
 * live: another member's (theirs to revoke), or this person's own whose
 * revoke failed. Empty when the replaced key is gone. */
export function leftLiveNote(leftLiveUserId: string, viewerId: string | null): string {
  if (!leftLiveUserId) return "";
  if (leftLiveUserId === viewerId) return "The old key could not be revoked; revoke it under Settings, API keys.";
  return "The old key belongs to another member and is still live in their API keys.";
}
