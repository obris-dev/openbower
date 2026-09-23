"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Spinner } from "@bower/ui";
import { fetchAgentCatalog } from "@bower/api";

import { etaSeconds, formatEta, pushSample, type EtaSample } from "./lib/fill-eta";
import { staleWarning } from "./lib/fill-staleness";
import type { LiveRun } from "./lib/live-status";

function count(n: number): string {
  return n.toLocaleString("en-US");
}

// The staleness clock's resolution (binary, about half a minute): the
// copy speaks in minutes against the stale threshold, so finer ticks buy
// nothing. Render-time Date.now() is impure; the clock is state.
const STALENESS_TICK_MS = 32_768;

const STATUS_LABEL: Record<LiveRun["status"], string> = {
  pending: "Starting",
  running: "Filling",
};

/** A LIVE run's chip (the tracker popover's status line): counters
 * (attempted of the consented row count), Stop, and
 * heartbeat staleness as warning-role copy judged against the wire's
 * lease window (fill-staleness owns the judgment and the per-status
 * copy; a stale heartbeat is degraded REPORTING, never failure). The
 * prop TYPE holds the live-only contract: the poll ships live runs,
 * use-fill filters what it trusts, and Stop's echo removes the run
 * outright, so a terminal envelope cannot reach this chip and the
 * terminal branches do not exist. The tracker cell renders terminal
 * stories from the column summary instead. */
export function FillProgress({ run, onStop }: { run: LiveRun; onStop: () => Promise<string | null> }) {
  const [stopping, setStopping] = useState(false);
  const [actionError, setActionError] = useState("");
  const [now, setNow] = useState(0);
  // Observed-rate samples, a REF the effect below pushes; render never
  // reads it (the ETA crosses into state from the same deferred
  // callback that moves the clock).
  const etaSamples = useRef<EtaSample[]>([]);
  // The ETA is OBSERVED rate only (the spend footer never promises a
  // duration): it appears once two progressing samples exist and
  // reflects whatever rate the workers achieved.
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);

  useEffect(() => {
    etaSamples.current = pushSample(etaSamples.current, Date.now(), run.counters.attempted);
    // Seeded from a zero-delay callback (render must stay pure and an
    // effect body must not set state synchronously); the interval then
    // keeps the copy's minutes moving even when polls blip.
    const refresh = () => {
      setNow(Date.now());
      setRemainingSeconds(etaSeconds(etaSamples.current, run.confirmed_row_count - run.counters.attempted));
    };
    const seed = setTimeout(refresh, 0);
    const timer = setInterval(refresh, STALENESS_TICK_MS);
    return () => {
      clearTimeout(seed);
      clearInterval(timer);
    };
  }, [run]);

  // The not-started warning composes the deployment's support_followup
  // fragment, fetched only once that warning is due (a healthy fill
  // never pays for the catalog here). Absence degrades to the bare
  // sentence: the fragment renders only when the fact arrived.
  const [supportFollowup, setSupportFollowup] = useState<string | null>(null);
  const needsFollowup = run.status === "pending" && staleWarning(run, now) !== null;
  useEffect(() => {
    if (!needsFollowup || supportFollowup !== null) return;
    let superseded = false;
    async function load() {
      const res = await fetchAgentCatalog();
      if (!superseded && res.status === "ok") setSupportFollowup(res.data.support_followup);
    }
    void load();
    return () => {
      superseded = true;
    };
  }, [needsFollowup, supportFollowup]);

  // While the walk that queues the run's rows is still going (seconds
  // after the click) the denominator is the consent, not yet the
  // target set: the chip says what is happening rather than "0 of N".
  const counters =
    run.targeted_at === null && run.counters.attempted === 0
      ? `queuing ${count(run.confirmed_row_count)} rows`
      : `${count(run.counters.attempted)} of ${count(run.confirmed_row_count)} | ${count(
          run.counters.filled,
        )} filled | ${count(run.counters.blank)} blank${remainingSeconds !== null ? ` | ${formatEta(remainingSeconds)} remaining` : ""}`;
  const warning = staleWarning(run, now, supportFollowup ?? undefined);

  async function stop() {
    if (stopping) return;
    setStopping(true);
    setActionError("");
    const message = await onStop();
    setStopping(false);
    if (message) setActionError(message);
  }

  return (
    <div className="flex min-w-0 flex-col items-start gap-1">
      <div className="flex w-full items-center gap-2 rounded-full border border-hairline bg-wash py-1 pl-3 pr-1 text-xs">
        <Spinner className="h-3 w-3 shrink-0" />
        <span className="min-w-0 flex-1 text-muted">
          {STATUS_LABEL[run.status]} | {counters}
        </span>
        <Button size="sm" variant="ghost" loading={stopping} onClick={() => void stop()}>
          Stop
        </Button>
      </div>
      {warning && <p className="text-xs text-warning">{warning}</p>}
      {actionError && (
        // Tier 1 for Stop's refusal: the server wrote the detail, the
        // chip renders it verbatim.
        <p className="max-w-md text-left text-xs text-danger">{actionError}</p>
      )}
    </div>
  );
}
