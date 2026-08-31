import { ROW_LEASE_STALE_SECONDS, type FillRunWire } from "@bower/api";

import { isLiveStatus } from "./live-status.ts";

/** The staleness warning for a live fill, or null while reporting is
 * fresh (or the clock has not ticked). Judged against the wire's lease
 * window only; a stale heartbeat is degraded REPORTING, never failure.
 * The copy claims only what the client can see: a run still `pending`
 * past the threshold was never claimed ("hasn't started", composing
 * the deployment's support_followup fragment when that fact arrived);
 * a RUNNING run with no heartbeat yet has been claimed but its first
 * rows have not reported (the heartbeat stamps only on row completion,
 * so "hasn't started" would be false there); a gone-quiet heartbeat
 * names the silence in minutes. */
export function staleWarning(
  run: Pick<FillRunWire, "status" | "heartbeat_at" | "created_at">,
  nowMs: number,
  supportFollowup?: string,
): string | null {
  if (!isLiveStatus(run.status)) return null;
  const reportedAt = Date.parse(run.heartbeat_at ?? run.created_at);
  if (nowMs === 0 || Number.isNaN(reportedAt)) return null;
  const silentSeconds = (nowMs - reportedAt) / 1_000;
  if (silentSeconds <= ROW_LEASE_STALE_SECONDS) return null;
  if (run.status === "pending") {
    return supportFollowup ? `The fill hasn't started; ${supportFollowup}.` : "The fill hasn't started.";
  }
  if (run.heartbeat_at === null) return "The fill hasn't reported yet.";
  return `The worker hasn't reported in ${Math.floor(silentSeconds / 60)}m.`;
}
