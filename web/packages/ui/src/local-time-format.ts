// How a moment reads, in a named zone: the pure half of LocalTime, so the
// formats are testable in any zone the test names.

/** The ways the app shows a moment: the day it happened, the day and the
 * minute, or a STAMP, the minute with its year (for a moment that can
 * fall in any year). */
export const TIME_FORMAT = { DAY: "day", MINUTE: "minute", STAMP: "stamp" } as const;
export type TimeFormat = (typeof TIME_FORMAT)[keyof typeof TIME_FORMAT];

const OPTIONS: Record<TimeFormat, Intl.DateTimeFormatOptions> = {
  [TIME_FORMAT.DAY]: { month: "short", day: "numeric" },
  [TIME_FORMAT.MINUTE]: { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" },
  [TIME_FORMAT.STAMP]: { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" },
};

/** The moment in `timeZone` (the reader's own when undefined), or "" for
 * a value that is not a time. A time of day names its zone only when
 * it is not the reader's, since a bare time reads as local. */
export function formatMoment(iso: string, format: TimeFormat, timeZone?: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const options = OPTIONS[format];
  const named = timeZone !== undefined && options.hour !== undefined;
  return at.toLocaleString("en-US", { ...options, timeZone, timeZoneName: named ? "short" : undefined });
}
