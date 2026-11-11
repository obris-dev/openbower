import type { ColumnFillSummary, FillRunWire } from "@bower/api";

/** A run whose status is one of the two LIVE members: what the fills
 * page ships, held by construction on the client rather than trusted
 * (use-fill filters the page through the predicate below). */
export type LiveRun = FillRunWire & { status: "pending" | "running" };

/** The live half of the run-status vocabulary, named ONCE: the poll
 * loop's exit, the glance's counts, and the staleness judgment all
 * ask it, and two inline spellings drifting apart is the failure
 * mode a shared predicate exists to prevent. */
export function isLiveStatus(status: string): status is LiveRun["status"] {
  return status === "pending" || status === "running";
}

export type LivenessRead = { runs: LiveRun[]; live: boolean };

/** The poll's ONE liveness decision, pure so the node lane can pin
 * it. `runs` is what the client TRUSTS: the page filtered through
 * the predicate, so a run wearing a KNOWN terminal member never
 * renders Stop or keeps the loop alive by mere membership. (An
 * unknown status never reaches this function: the RUNS leg's
 * tolerant read normalizes it to running upstream, deliberately
 * keeping the loop alive; the SUMMARIES leg maps an unknown to "",
 * which reads as not-live and claims nothing.) `live` is whether
 * polling continues, true
 * while EITHER half of the payload says work is happening: the two
 * halves are read by separate queries server-side, so one poll can
 * carry a fill in only one of them, and a loop that exited on the
 * runs leg alone left the glance promising updates with nothing
 * polling behind them. */
export function livenessRead(
  runs: readonly FillRunWire[],
  summaries: readonly Pick<ColumnFillSummary, "current_status">[],
): LivenessRead {
  const trusted = runs.filter((run): run is LiveRun => isLiveStatus(run.status));
  return {
    runs: trusted,
    live: trusted.length > 0 || summaries.some((summary) => isLiveStatus(summary.current_status)),
  };
}
