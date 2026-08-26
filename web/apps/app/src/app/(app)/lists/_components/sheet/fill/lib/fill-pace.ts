import type { FillWire } from "@bower/api";

/** The speed breakdown, phrased: derived from the worker's pace
 * accumulators (wall seconds across terminal rows, the summed search
 * waits inside them, the AIMD operating point at the last write).
 * Null until a row has landed. Search waits can EXCEED wall time
 * (parallel tool calls sum), so the share claim is qualitative, never
 * a percentage pretending precision. */
export function paceSummary(counters: FillWire["counters"]): string | null {
  const { attempted, row_seconds, search_wait_seconds, concurrency_point } = counters;
  if (attempted <= 0 || row_seconds <= 0) return null;
  const avgRow = Math.round(row_seconds / attempted);
  const parts = [`~${avgRow} s per row`];
  const searchShare = search_wait_seconds / row_seconds;
  if (searchShare >= 0.5) parts.push("mostly waiting on search");
  else if (searchShare > 0) parts.push(`~${Math.round(search_wait_seconds / attempted)} s of it search`);
  if (concurrency_point > 0) parts.push(`${concurrency_point} wide`);
  return parts.join(" | ");
}
