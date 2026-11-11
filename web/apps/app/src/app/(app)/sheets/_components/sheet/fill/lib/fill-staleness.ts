import { ROW_LEASE_STALE_SECONDS, type FillRunWire } from "@bower/api";

import { isLiveStatus } from "./live-status.ts";

/** The staleness warning for a live fill, or null while reporting is
 * fresh (or the clock has not ticked). Judged against the wire's lease
 * window only; a stale heartbeat is degraded REPORTING, never failure.
 * The copy claims only what the client can see: a run still `pending`
 * past the threshold was never claimed ("hasn't started", composing
 * the deployment's support_followup fragment when that fact arrived);
 * a gone-quiet heartbeat names the silence in minutes (the heartbeat is
 * the fill's latest movement, a run's or the job's own, so it is never
 * missing). A run whose target set is not whole yet (`targeted_at`
 * null) is still being queued and warns of nothing. */
export function staleWarning(
  run: Pick<FillRunWire, "status" | "heartbeat_at" | "targeted_at">,
  nowMs: number,
  supportFollowup?: string,
): string | null {
  if (!isLiveStatus(run.status)) return null;
  // A run whose rows are still being queued has not been offered to a
  // worker yet: "hasn't started" would blame the workers for the walk.
  if (run.targeted_at === null) return null;
  const reportedAt = Date.parse(run.heartbeat_at);
  if (nowMs === 0 || Number.isNaN(reportedAt)) return null;
  const silentSeconds = (nowMs - reportedAt) / 1_000;
  if (silentSeconds <= ROW_LEASE_STALE_SECONDS) return null;
  if (run.status === "pending") {
    return supportFollowup ? `The fill hasn't started; ${supportFollowup}.` : "The fill hasn't started.";
  }
  return `The worker hasn't reported in ${Math.floor(silentSeconds / 60)}m.`;
}
