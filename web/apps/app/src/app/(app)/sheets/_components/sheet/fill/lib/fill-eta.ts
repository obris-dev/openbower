/** The observed-rate ETA: extrapolated from the fill's ACTUAL
 * throughput (samples of the attempted counter over time), never a
 * pre-run figure, so it reflects whatever operating point the
 * worker's adaptive concurrency found. Pure: the component owns the
 * clock and the sample state. */

export type EtaSample = { at: number; attempted: number };

// The sliding window (binary): enough samples to smooth one slow row,
// short enough to track a concurrency climb within a few minutes.
export const ETA_MAX_SAMPLES = 16;

/** Append a sample when attempted moved; identical or regressed
 * counts are not progress (a refresh re-serving the same envelope
 * must not dilute the rate). */
export function pushSample(samples: EtaSample[], at: number, attempted: number): EtaSample[] {
  const last = samples.at(-1);
  if (last !== undefined && attempted <= last.attempted) return samples;
  return [...samples, { at, attempted }].slice(-ETA_MAX_SAMPLES);
}

/** Seconds remaining, or null while the rate is not yet honest (fewer
 * than two samples, no elapsed time, or nothing left). */
export function etaSeconds(samples: EtaSample[], remaining: number): number | null {
  if (remaining <= 0 || samples.length < 2) return null;
  const first = samples[0];
  const last = samples.at(-1);
  if (first === undefined || last === undefined) return null;
  const elapsed = (last.at - first.at) / 1000;
  const progressed = last.attempted - first.attempted;
  if (elapsed <= 0 || progressed <= 0) return null;
  return Math.round(remaining / (progressed / elapsed));
}

/** "~40 sec" | "~12 min" | "~2 h 10 min": one approximate unit pair,
 * the ~ carrying the honesty (an estimate says it is one). */
export function formatEta(seconds: number): string {
  if (seconds < 60) return "~1 min";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `~${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `~${hours} h` : `~${hours} h ${rest} min`;
}
