// How a moment reads, in a named zone: the pure half of LocalTime, so the
// formats are testable in any zone the test names.

/** The two ways the app shows a moment: the day it happened, or the day
 * and the minute. */
export const TIME_FORMAT = { DAY: "day", MINUTE: "minute" } as const;
export type TimeFormat = (typeof TIME_FORMAT)[keyof typeof TIME_FORMAT];

const OPTIONS: Record<TimeFormat, Intl.DateTimeFormatOptions> = {
  [TIME_FORMAT.DAY]: { month: "short", day: "numeric" },
  [TIME_FORMAT.MINUTE]: { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" },
};

/** The moment in `timeZone` (the reader's own when undefined), or "" for
 * a value that is not a time. A minute names its zone only when it is
 * not the reader's, since a bare time reads as local. */
export function formatMoment(iso: string, format: TimeFormat, timeZone?: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const named = timeZone !== undefined && format === TIME_FORMAT.MINUTE;
  return at.toLocaleString("en-US", { ...OPTIONS[format], timeZone, timeZoneName: named ? "short" : undefined });
}
