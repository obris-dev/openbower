"use client";

import { useEffect, useRef, useState } from "react";
import { Button, cn, Spinner } from "@bower/ui";
import { fetchAgentCatalog, type FillRunWire } from "@bower/api";

import { etaSeconds, formatEta, pushSample, type EtaSample } from "./lib/fill-eta";
import { paceSummary } from "./lib/fill-pace";
import { staleWarning } from "./lib/fill-staleness";

function count(n: number): string {
  return n.toLocaleString("en-US");
}

// The staleness clock's resolution (binary, about half a minute): the
// copy speaks in minutes against the stale threshold, so finer ticks buy
// nothing. Render-time Date.now() is impure; the clock is state.
const STALENESS_TICK_MS = 32_768;

const STATUS_LABEL: Record<FillRunWire["status"], string> = {
  pending: "Starting",
  running: "Filling",
  complete: "Done",
  failed: "Fill failed",
  cancelled: "Stopped",
};

/** One fill's chip in the sheet's status bar: counters (attempted of
 * the consented row count), Stop while live, heartbeat staleness as
 * warning-role copy judged against the wire's lease window
 * (fill-staleness owns the judgment and the per-status copy; a stale
 * heartbeat is degraded REPORTING, never failure; only terminal states
 * end the story), the failed run's server-written error verbatim, and
 * quiet done/stopped states with the final counters. A stopped or
 * failed run carries its recovery verb in place: Continue starts a
 * NEW run over the unanswered rows (a refusal renders verbatim in
 * the chip's error slot). A terminal chip simply persists until the
 * next run supersedes it: no dismissal exists, so a fill's management
 * surface can never be hidden by mistake. Two contexts, one chip:
 * "inline" right-aligns and truncates against the footer's one line;
 * "panel" fills the tray panel's width, left-aligned, and lets the
 * counters wrap so nothing is lost. */
export function FillProgress({
  run,
  onStop,
  onContinue,
  context = "inline",
}: {
  run: FillRunWire;
  onStop: () => Promise<string | null>;
  onContinue: () => Promise<string | null>;
  context?: "inline" | "panel";
}) {
  const [stopping, setStopping] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const [actionError, setActionError] = useState("");
  const [now, setNow] = useState(0);
  // Observed-rate samples, a REF the effect below pushes; render never
  // reads it (the ETA crosses into state from the same deferred
  // callback that moves the clock).
  const etaSamples = useRef<EtaSample[]>([]);
  // The ETA is OBSERVED rate only (the spend footer never promises a
  // duration): it appears once two progressing samples exist and
  // reflects whatever concurrency the worker's controller found.
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);

  const live = run.status === "pending" || run.status === "running";

  useEffect(() => {
    etaSamples.current = pushSample(etaSamples.current, Date.now(), run.counters.attempted);
    // Seeded from a zero-delay callback (render must stay pure and an
    // effect body must not set state synchronously); the interval then
    // keeps the copy's minutes moving even when polls blip.
    const refresh = () => {
      setNow(Date.now());
      setRemainingSeconds(
        live ? etaSeconds(etaSamples.current, run.confirmed_row_count - run.counters.attempted) : null,
      );
    };
    const seed = setTimeout(refresh, 0);
    if (!live) return () => clearTimeout(seed);
    const timer = setInterval(refresh, STALENESS_TICK_MS);
    return () => {
      clearTimeout(seed);
      clearInterval(timer);
    };
  }, [live, run]);

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

  const counters = `${count(run.counters.attempted)} of ${count(run.confirmed_row_count)} | ${count(
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

  async function continueFill() {
    if (continuing) return;
    setContinuing(true);
    setActionError("");
    const message = await onContinue();
    setContinuing(false);
    if (message) setActionError(message);
  }

  const inPanel = context === "panel";
  // The two recovery verbs belong to the stopped and failed stories
  // only: a complete fill has nothing to continue and its chip
  // retires on its own once the poll loop ends.
  const recoverable = run.status === "cancelled" || run.status === "failed";

  return (
    <div className={cn("flex min-w-0 flex-col gap-1", inPanel ? "items-start" : "items-end")}>
      <div
        className={cn(
          "flex items-center gap-2 rounded-full border border-hairline bg-wash py-1 pl-3 pr-1 text-xs",
          inPanel && "w-full",
        )}
      >
        {live && <Spinner className="h-3 w-3 shrink-0" />}
        <span
          className={cn(
            inPanel ? "min-w-0 flex-1" : "truncate",
            run.status === "failed" ? "text-danger" : "text-muted",
          )}
        >
          {STATUS_LABEL[run.status]} | {counters}
        </span>
        {live ? (
          <Button size="sm" variant="ghost" loading={stopping} onClick={() => void stop()}>
            Stop
          </Button>
        ) : recoverable ? (
          <span className="flex shrink-0 items-center">
            <Button size="sm" variant="ghost" loading={continuing} onClick={() => void continueFill()}>
              Continue
            </Button>
          </span>
        ) : (
          // Keeps the pill's height stable across the live-to-terminal
          // edge without a phantom control.
          <span className="py-1" aria-hidden />
        )}
      </div>
      {live && paceSummary(run.counters) && (
        <p className="text-xs text-faint">{paceSummary(run.counters)}</p>
      )}
      {warning && <p className="text-xs text-warning">{warning}</p>}
      {run.status === "failed" && (
        // Tier 1: the server wrote this copy; render it verbatim. The
        // no-error fallback stays plain rather than guessing a cause.
        <p className={cn("max-w-md text-xs text-danger", inPanel ? "text-left" : "text-right")}>
          {run.error?.message ?? "The fill failed."}
        </p>
      )}
      {actionError && (
        // Tier 1 for Stop's and Continue's refusals alike: the server
        // wrote the detail, the chip renders it verbatim.
        <p className={cn("max-w-md text-xs text-danger", inPanel ? "text-left" : "text-right")}>{actionError}</p>
      )}
    </div>
  );
}
