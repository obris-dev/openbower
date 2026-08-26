"use client";

import { useEffect, useRef, useState } from "react";
import { Button, cn, Popover, PopoverButton, PopoverPanel, Spinner } from "@bower/ui";
import type { FillWire } from "@bower/api";

import { etaSeconds, pushSample, type EtaSample } from "./lib/fill-eta";
import { FillProgress } from "./fill-progress";
import { anyFailed, anyLive, badgeLabel, isLiveStatus, soonestEta, trayMode } from "./lib/fills-tray-mode";

/** The footer's fills area, condensing instead of wrapping (the footer
 * band stays one line). One job with room (sm and up) renders its chip
 * inline as before; several jobs, or a narrow viewport, collapse to a
 * summary badge (spinner while live, danger-tinted when any job
 * failed, soonest observed ETA appended) that discloses an upward
 * panel stacking every job's full chip: counters, ETA, Stop, staleness
 * warning, the failed error verbatim. The Popover primitive carries
 * the disclosure floor (aria-expanded, Escape, outside-click, focus
 * return); the breakpoint halves are CSS classes, so JS holds only the
 * open state. The single-job breakpoint swap mounts the chip twice
 * (hidden inline + panel), a deliberate trade for a CSS-only swap with
 * no hydration-visible width read. */
// A tray Continue is FILL-scoped, not column-scoped: it resumes the
// whole fill, and the server judges what that fill still owes across
// EVERY column it owns. The endpoint is column-addressed, so any of
// them names the same fill and the same target set; the first is
// simply the stable choice. A fill always declares at least one
// column, so the fallback is a contract gap and routes to the generic
// failure rather than guessing.
function resumeColumn(job: FillWire): string {
  return job.column_keys[0] ?? "";
}

export function FillsTray({
  jobs,
  onStop,
  onContinue,
}: {
  jobs: FillWire[];
  onStop: (jobId: string) => Promise<string | null>;
  onContinue: (job: FillWire, columnKey: string, opts?: { rows?: number; resume?: boolean }) => Promise<string | null>;
}) {
  // The badge's soonest-ETA samples, per job: each chip's own window
  // lives inside its FillProgress and is not reachable here, so the
  // tray keeps its own. A ref the effect pushes (ended or vanished
  // jobs drop their windows); the soonest ETA crosses into state from
  // a zero-delay callback (render must stay pure and an effect body
  // must not set state synchronously), and each poll's new jobs array
  // re-runs the effect, so no interval is needed.
  const samplesRef = useRef<Map<string, EtaSample[]>>(new Map());
  const [eta, setEta] = useState<number | null>(null);
  useEffect(() => {
    const at = Date.now();
    const next = new Map<string, EtaSample[]>();
    for (const job of jobs) {
      if (!isLiveStatus(job.status)) continue;
      next.set(job.id, pushSample(samplesRef.current.get(job.id) ?? [], at, job.counters.attempted));
    }
    samplesRef.current = next;
    const seed = setTimeout(() => {
      setEta(
        soonestEta(
          jobs.map((job) =>
            isLiveStatus(job.status)
              ? etaSeconds(next.get(job.id) ?? [], job.confirmed_row_count - job.counters.attempted)
              : null,
          ),
        ),
      );
    }, 0);
    return () => clearTimeout(seed);
  }, [jobs]);

  const mode = trayMode(jobs.length);
  if (mode === "empty") return null;

  const single = mode === "single";
  const live = anyLive(jobs);
  const failed = anyFailed(jobs);
  const label = badgeLabel(jobs, eta);

  return (
    <div className="flex min-w-0 flex-col items-end">
      {single &&
        jobs.map((job) => (
          <div key={job.id} className="hidden min-w-0 sm:block">
            <FillProgress job={job} onStop={() => onStop(job.id)} onContinue={() => onContinue(job, resumeColumn(job), { resume: true })} />
          </div>
        ))}
      <Popover className={cn("min-w-0", single && "sm:hidden")}>
        <PopoverButton
          as={Button}
          size="sm"
          variant="ghost"
          className={cn(
            "max-w-full gap-2 rounded-full border border-hairline bg-wash px-3 py-1 text-xs font-normal hover:bg-wash-strong",
            failed ? "text-danger" : "text-muted",
          )}
        >
          {live && <Spinner className="h-3 w-3 shrink-0" />}
          {/* The visible text is the whole story ("2 fills | ~41 min");
              the sr-only prefix names it only where the compact
              counters alone would not. */}
          {single && <span className="sr-only">Fill progress: </span>}
          <span className="min-w-0 truncate">{label}</span>
        </PopoverButton>
        {/* The panel portals out of this subtree, so the single-mode
            breakpoint class rides the panel itself too. */}
        <PopoverPanel anchor="top end" className={cn("motion-reduce:transition-none", single && "sm:hidden")}>
          <div className="flex max-h-[60vh] w-[min(26rem,calc(100vw-2rem))] flex-col gap-3 overflow-y-auto p-3">
            {jobs.map((job) => (
              <FillProgress
                key={job.id}
                context="panel"
                job={job}
                onStop={() => onStop(job.id)}
                onContinue={() => onContinue(job, resumeColumn(job), { resume: true })}
              />
            ))}
          </div>
        </PopoverPanel>
      </Popover>
    </div>
  );
}
