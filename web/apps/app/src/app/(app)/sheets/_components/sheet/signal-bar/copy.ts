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

/** Each period as the picker names it. */
export const PERIOD_NAMES: Record<Period, string> = {
  [PERIOD.DAY]: "Last 24 hours",
  [PERIOD.WEEK]: "Last 7 days",
  [PERIOD.MONTH]: "Last 30 days",
  [PERIOD.QUARTER]: "Last 90 days",
  [PERIOD.HALF_YEAR]: "Last 6 months",
};
