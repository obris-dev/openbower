"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { TouchTarget } from "@bower/ui";

import { rowLine } from "../copy";
import type { SampleStepper } from "./types";

/** Which loaded row is the sample: prev/next over the page the sheet
 * has in memory, a plain label when there is only one. */
export function RowStepper({ sample }: { sample: SampleStepper }) {
  // `relative` anchors the TouchTarget's 44px hit area (coarse pointers
  // only), so the buttons stay small on screen.
  const stepClass =
    "relative rounded-md p-1 text-faint hover:bg-wash hover:text-foreground disabled:opacity-40 disabled:hover:bg-transparent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-600";
  const line = rowLine(sample.position, sample.count);
  if (sample.count <= 1) return <span className="shrink-0 text-xs text-muted tabular-nums">{line}</span>;
  return (
    <div className="flex shrink-0 items-center gap-1 text-xs text-muted" role="group" aria-label="Sample row">
      <button
        type="button"
        onClick={() => sample.onStep(-1)}
        disabled={sample.index === 0}
        aria-label="Previous row"
        className={stepClass}
      >
        <TouchTarget>
          <ChevronLeft aria-hidden className="h-4 w-4" />
        </TouchTarget>
      </button>
      <span className="tabular-nums">{line}</span>
      <button
        type="button"
        onClick={() => sample.onStep(1)}
        disabled={sample.index >= sample.count - 1}
        aria-label="Next row"
        className={stepClass}
      >
        <TouchTarget>
          <ChevronRight aria-hidden className="h-4 w-4" />
        </TouchTarget>
      </button>
    </div>
  );
}
