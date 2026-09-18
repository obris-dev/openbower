// The cadence a webhook column sends on: one of the contract's presets,
// rendered as a duration a person reads.

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86_400;

/** "5 minutes" | "1 hour" | "6 hours" | "1 day": the largest whole unit
 * that divides the value, so a non-preset value a server still holds
 * reads by the same rule. */
export function cadenceLabel(seconds: number): string {
  if (seconds % DAY === 0) return plural(seconds / DAY, "day");
  if (seconds % HOUR === 0) return plural(seconds / HOUR, "hour");
  return plural(Math.round(seconds / MINUTE), "minute");
}

function plural(count: number, unit: string): string {
  return `${count} ${unit}${count === 1 ? "" : "s"}`;
}

/** The select's options: the contract's presets plus the stored value
 * when it is not one of them (a server holding an older preset must
 * render the truth it holds, never silently re-pick and dirty the
 * form). Ascending, no duplicates. */
export function cadenceOptions(presets: readonly number[], current: number): number[] {
  return [...new Set([...presets, current])].sort((a, b) => a - b);
}
