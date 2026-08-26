import { ROW_LEASE_STALE_SECONDS, type FillWire } from "@bower/api";

/** The staleness warning for a live fill, or null while reporting is
 * fresh (or the clock has not ticked). Judged against the wire's lease
 * window only; a stale heartbeat is degraded REPORTING, never failure.
 * The copy claims only what the client can see: a job still `pending`
 * past the threshold was never claimed ("hasn't started", composing
 * the deployment's support_followup fragment when that fact arrived);
 * a RUNNING job with no heartbeat yet has been claimed but its first
 * rows have not reported (the heartbeat stamps only on row completion,
 * so "hasn't started" would be false there); a gone-quiet heartbeat
 * names the silence in minutes. */
export function staleWarning(
  job: Pick<FillWire, "status" | "heartbeat_at" | "created_at">,
  nowMs: number,
  supportFollowup?: string,
): string | null {
  if (job.status !== "pending" && job.status !== "running") return null;
  const reportedAt = Date.parse(job.heartbeat_at ?? job.created_at);
  if (nowMs === 0 || Number.isNaN(reportedAt)) return null;
  const silentSeconds = (nowMs - reportedAt) / 1_000;
  if (silentSeconds <= ROW_LEASE_STALE_SECONDS) return null;
  if (job.status === "pending") {
    return supportFollowup ? `The fill hasn't started; ${supportFollowup}.` : "The fill hasn't started.";
  }
  if (job.heartbeat_at === null) return "The fill hasn't reported yet.";
  return `The worker hasn't reported in ${Math.floor(silentSeconds / 60)}m.`;
}
