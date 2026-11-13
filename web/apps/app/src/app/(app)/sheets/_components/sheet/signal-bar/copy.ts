// A signal's sheet: its bar's copy, one place.
import { PERIOD, type Period } from "../lib/periods.ts";

export const LAST_READ = "Last read";
export const NOT_YET_READ = "Not yet read";
export const PERIOD_PICKER_LABEL = "Period";
export const REFRESH_ACTION = "Refresh";
export const REFRESH_FAILED = "This page could not read the signal's latest rows; try again.";
export const LIVE_LABEL = "Live";
/** An empty period's line: the period holds no rows, the sheet may. */
export function emptyPeriodNote(period: Period): string {
  const name = PERIOD_NAMES[period];
  const lowered = name.toLowerCase();
  return `No rows logged in the ${lowered}.`;
}

// The banner: what stops new rounds, and what a person does about it.
export const BANNER_LISTENER_GONE =
  "This signal's listener is gone from the signals service, so no new rounds will arrive. Its rows stay here.";
export const BANNER_RECONNECT = "New rounds are paused: this account's connection to signals stopped working.";
export const BANNER_FULL = "This sheet holds as many rows as it may, so no new rounds arrive. Delete rows to make room.";
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

/** Each period as the picker names it. */
export const PERIOD_NAMES: Record<Period, string> = {
  [PERIOD.DAY]: "Last 24 hours",
  [PERIOD.WEEK]: "Last 7 days",
  [PERIOD.MONTH]: "Last 30 days",
  [PERIOD.QUARTER]: "Last 90 days",
  [PERIOD.HALF_YEAR]: "Last 6 months",
};
