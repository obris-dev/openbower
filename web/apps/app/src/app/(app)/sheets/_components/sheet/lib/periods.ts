// A signal's sheet reads one recent period at a time: rolling windows
// back from now (a signal is temporal; its relevance is its recency), so
// the bound needs no time zone and the server can draw the first page.

export const PERIOD = {
  DAY: "24h",
  WEEK: "7d",
  MONTH: "30d",
  QUARTER: "90d",
  HALF_YEAR: "6m",
} as const;
export type Period = (typeof PERIOD)[keyof typeof PERIOD];

export const DEFAULT_PERIOD: Period = PERIOD.WEEK;

/** The periods in the picker's order, shortest first. */
export const PERIODS: readonly Period[] = [PERIOD.DAY, PERIOD.WEEK, PERIOD.MONTH, PERIOD.QUARTER, PERIOD.HALF_YEAR];

const KNOWN_PERIODS = new Set<string>(PERIODS);
const HOUR_MS = 3_600_000;
const DAYS_BACK: Record<Exclude<Period, typeof PERIOD.HALF_YEAR>, number> = {
  [PERIOD.DAY]: 1,
  [PERIOD.WEEK]: 7,
  [PERIOD.MONTH]: 30,
  [PERIOD.QUARTER]: 90,
};
const HALF_YEAR_MONTHS = 6;

/** A period off the URL; anything else reads as the default. */
export function periodFrom(raw: string | string[] | undefined): Period {
  return typeof raw === "string" && KNOWN_PERIODS.has(raw) ? (raw as Period) : DEFAULT_PERIOD;
}

/** The period's start, back from `now`, as the instant the rows' `?since=`
 * takes. The half year steps back six calendar months. */
export function sinceFor(period: Period, now: number): string {
  if (period === PERIOD.HALF_YEAR) {
    const start = new Date(now);
    start.setUTCMonth(start.getUTCMonth() - HALF_YEAR_MONTHS);
    return start.toISOString();
  }
  const back = DAYS_BACK[period] * 24 * HOUR_MS;
  return new Date(now - back).toISOString();
}
