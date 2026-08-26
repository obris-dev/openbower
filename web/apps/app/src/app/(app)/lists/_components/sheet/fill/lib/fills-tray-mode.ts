import type { FillWire } from "@bower/api";

import { formatEta } from "./fill-eta.ts";

/** Pure decisions for the fills tray: which rendering shape applies
 * and what the summary badge says. Width is CSS's half (the tray's
 * responsive classes); these functions own the count-and-status half.
 *
 * The shape table the tray renders from:
 * | jobs | below sm      | sm and up     |
 * | 0    | nothing       | nothing       |
 * | 1    | badge + panel | inline chip   |
 * | 2+   | badge + panel | badge + panel |
 */

/** The slice of the job envelope these decisions read (FillWire
 * satisfies it structurally). */
export type TrayJob = Pick<FillWire, "status" | "confirmed_row_count"> & {
  counters: Pick<FillWire["counters"], "attempted">;
};

export type TrayMode = "empty" | "single" | "multi";

/** No jobs renders nothing; one job's chip goes inline where width
 * allows (the badge below sm); two or more always condense to the
 * badge, since N chips cannot share the footer's one line. */
export function trayMode(jobCount: number): TrayMode {
  if (jobCount === 0) return "empty";
  return jobCount === 1 ? "single" : "multi";
}

export function isLiveStatus(status: TrayJob["status"]): boolean {
  return status === "pending" || status === "running";
}

/** The badge's spinner rides any live job. */
export function anyLive(jobs: readonly TrayJob[]): boolean {
  return jobs.some((job) => isLiveStatus(job.status));
}

/** Any failed job tints the badge with the danger role: the collapse
 * must never hide a failure. */
export function anyFailed(jobs: readonly TrayJob[]): boolean {
  return jobs.some((job) => job.status === "failed");
}

/** The soonest of the per-job observed-rate ETAs, or null while none
 * is known (the badge then speaks counters alone). */
export function soonestEta(etas: ReadonlyArray<number | null>): number | null {
  let soonest: number | null = null;
  for (const eta of etas) {
    if (eta !== null && (soonest === null || eta < soonest)) soonest = eta;
  }
  return soonest;
}

function count(n: number): string {
  return n.toLocaleString("en-US");
}

/** The badge's text: one fill keeps its compact counters ("312 of
 * 2,568"); several collapse to a count ("2 fills"); the soonest known
 * ETA appends to either ("2 fills | ~41 min"). */
export function badgeLabel(jobs: readonly TrayJob[], soonestEtaSeconds: number | null): string {
  const only = jobs.length === 1 ? jobs[0] : undefined;
  const head =
    only !== undefined
      ? `${count(only.counters.attempted)} of ${count(only.confirmed_row_count)}`
      : `${count(jobs.length)} fills`;
  return soonestEtaSeconds === null ? head : `${head} | ${formatEta(soonestEtaSeconds)}`;
}
