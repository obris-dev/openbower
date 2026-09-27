import type { RenderableListRow } from "@bower/api";

// The first re-read while a cell shows pending (binary): the fill
// poll's own cadence, so a quick autofill lands about as fast as a
// fill's cell does.
export const PENDING_REFRESH_FIRST_MS = 4_096;
// The ceiling (binary): a webhook send can wait out a long interval
// window, and a cell pending for an hour must not re-read every loaded
// page every few seconds for that hour.
export const PENDING_REFRESH_CEILING_MS = 65_536;
// Consecutive failed reads before a page says its updates are not
// arriving: one blip is nothing, and the two loops that re-read the
// sheet (the fill poll and the pending re-read) share the bar so the
// one line they both light means the same thing.
export const MAX_POLL_ERRORS = 4;

/** Whether `failures` consecutive failed reads are trouble worth
 * telling the user about: a client-only fact (the page cannot see the
 * server), so it never claims anything about the work itself. */
export function readsAreTroubled(failures: number): boolean {
  return failures >= MAX_POLL_ERRORS;
}

/** Every loaded cell that reads pending, as one comparable string ("" when
 * none): work the server has queued that no fill poll reports (an autofill
 * on a pushed row, a webhook waiting for its window), so nothing else
 * re-reads the rows when it lands. A cell joining or leaving the set
 * changes the string, which is what restarts the re-read schedule. */
export function pendingSignature(rows: readonly Pick<RenderableListRow, "id" | "states">[]): string {
  const cells: string[] = [];
  for (const row of rows) {
    for (const [key, entry] of Object.entries(row.states ?? {})) {
      if (entry.state === "pending") cells.push(`${row.id}:${key}`);
    }
  }
  return cells.join(" ");
}

/** The wait before re-read `attempt` (0-based) while the pending set
 * holds still: doubling from the first interval up to the ceiling. */
export function pendingRefreshDelayMs(attempt: number): number {
  return Math.min(PENDING_REFRESH_FIRST_MS * 2 ** attempt, PENDING_REFRESH_CEILING_MS);
}
