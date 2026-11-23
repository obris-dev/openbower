"use client";

import { useState } from "react";
import { Play, Square } from "lucide-react";
import { Popover, PopoverButton, PopoverPanel, Spinner, useToast } from "@bower/ui";

import { FillColumnScope } from "./fill-column-scope";

/** What a column's header can do about its fill: start one (a fill
 * needs its rows named first: the next N, or all remaining) or stop
 * the one running. */
export type ColumnFill = {
  /** The column's live fill run, or null. */
  runId: string | null;
  /** The column starts fills itself (not behind a column it waits on). */
  canFill: boolean;
  fill: (maxRowCount?: number) => Promise<string | null>;
  stop: (runId: string) => Promise<string | null>;
};

const ICON_BUTTON =
  "flex h-6 w-6 shrink-0 items-center justify-center rounded text-faint outline-none hover:bg-wash hover:text-foreground focus-visible:ring-2 focus-visible:ring-signal";

/** The header's fill button: Play opens the scope (a fill spends, so
 * one click never starts one), and while the column's fill runs it is
 * Stop, which acts at once (stopping only saves). An autofill shows no
 * Stop: it runs per arriving row, and the sheet's Autofill switch is
 * its off. A column behind another shows nothing here. */
export function FillControl({ label, fill }: { label: string; fill: ColumnFill }) {
  const toast = useToast();
  const [stopping, setStopping] = useState(false);

  if (fill.runId !== null) {
    const runId = fill.runId;
    return (
      <button
        type="button"
        aria-label={`Stop the ${label} fill`}
        title="Stop fill"
        disabled={stopping}
        onClick={async () => {
          setStopping(true);
          const message = await fill.stop(runId);
          setStopping(false);
          if (message) toast.error(message, "Fill not stopped");
        }}
        className={ICON_BUTTON}
      >
        {stopping ? <Spinner className="h-3 w-3" /> : <Square aria-hidden className="h-3 w-3 fill-current" />}
      </button>
    );
  }
  if (!fill.canFill) return null;
  return (
    <Popover>
      <PopoverButton aria-label={`Fill the ${label} column`} title="Fill column" className={ICON_BUTTON}>
        <Play aria-hidden className="h-3.5 w-3.5" />
      </PopoverButton>
      <PopoverPanel anchor="bottom end" className="px-3 py-2.5">
        {({ close }) => (
          <FillColumnScope
            onFill={async (maxRowCount) => {
              const message = await fill.fill(maxRowCount);
              if (!message) close();
              return message;
            }}
          />
        )}
      </PopoverPanel>
    </Popover>
  );
}
