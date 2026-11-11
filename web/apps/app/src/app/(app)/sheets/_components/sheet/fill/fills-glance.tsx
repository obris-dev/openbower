"use client";

import { Spinner } from "@bower/ui";
import type { ColumnFillSummary } from "@bower/api";

import { failedLabel, fillingLabel, glanceCounts } from "./lib/glance-counts";

/** The footer's fills glance: a passive high-level read, in line with
 * the market reference (a small aggregate in the corner, the column
 * header owning real progress). It says only that columns are filling
 * or that a newest run failed, so neither fact can scroll off the
 * edge of a wide sheet with its tracker cell. Deliberately NOT a
 * control: no popover, no jump-to-column, no ETA, no counters; the
 * tracker row carries all of that per column. An idle sheet renders
 * nothing here. `liveRunIds` is the trusted live set's ids: the join
 * guarantees the column's own run, while it is on the live page, is
 * never counted failed; the status leg keeps the glance honest about
 * the server's last word during the one-tick windows where no
 * envelope rides the page (glance-counts states the asymmetry with
 * the tracker cell). */
export function FillsGlance({ summaries, liveRunIds }: { summaries: ColumnFillSummary[]; liveRunIds: string[] }) {
  const { filling, failed } = glanceCounts(summaries, new Set(liveRunIds));
  const fillingText = fillingLabel(filling);
  const failedText = failedLabel(failed);
  if (fillingText === null && failedText === null) return null;
  return (
    <p className="flex min-w-0 items-center gap-3 whitespace-nowrap text-xs">
      {fillingText !== null && (
        <span className="flex min-w-0 items-center gap-1.5 text-muted">
          <Spinner className="h-3 w-3 shrink-0" />
          <span className="truncate">{fillingText}</span>
        </span>
      )}
      {failedText !== null && <span className="truncate text-danger">{failedText}</span>}
    </p>
  );
}
