"use client";

import { useId, useState } from "react";
import { CornerDownLeft, FastForward, Play } from "lucide-react";
import { cn, ErrorMessage, Spinner } from "@bower/ui";

import { DEFAULT_SCOPE_ROWS, parseScopeRows } from "./lib/fill-scope";

function rows(n: number): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? "row" : "rows"}`;
}

/** A column's fill, from its header's Fill button or its menu's Fill
 * column: a titled panel of two choices, the next N rows never tried
 * or all of them, each saying what it covers before it spends. N is an
 * upper bound, not a promise (the server skips rows already tried and
 * owns the true eligible count), so the copy says "next". `left` is
 * what the column's summary says no attempt has reached; with nothing
 * left, both choices have nothing to do (a fill only runs rows never
 * tried) and the panel says so. Offered only
 * while no fill of the column runs: one run per column is the
 * invariant, so a fill during a live walk could only be refused. A
 * refusal renders verbatim below (tier 1: the server wrote it). */
export function FillColumnScope({
  label,
  left,
  onFill,
}: {
  label: string;
  left: number;
  onFill: (maxRowCount?: number) => Promise<string | null>;
}) {
  // Starts at the default or the rows left, whichever is fewer: a
  // count past what is left would only promise rows the fill skips.
  const [rowsText, setRowsText] = useState(() => String(left > 0 ? Math.min(DEFAULT_SCOPE_ROWS, left) : DEFAULT_SCOPE_ROWS));
  const [busy, setBusy] = useState<"next" | "all" | null>(null);
  const [refusal, setRefusal] = useState("");
  const next = parseScopeRows(rowsText);
  const inputId = useId();

  async function run(which: "next" | "all") {
    if (busy || left === 0 || (which === "next" && next === null)) return;
    setBusy(which);
    setRefusal("");
    const message = await onFill(which === "next" ? (next ?? undefined) : undefined);
    setBusy(null);
    if (message) setRefusal(message);
  }

  const row = "flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left text-sm";
  return (
    <div className="w-72 max-w-[calc(100vw-2rem)]">
      <div className="px-3 pb-2 pt-2.5">
        <p className="truncate text-sm font-medium text-foreground">Fill {label}</p>
        <p className="text-xs text-muted">{left > 0 ? `${rows(left)} left to fill` : "Every row has run"}</p>
      </div>
      <div className="space-y-0.5 border-t border-hairline p-1">
        {/* Not a button: it holds the count's input. The verb is the
            button; Enter in the input runs it too. */}
        <div className={cn(row, left > 0 && "hover:bg-wash")}>
          <button
            type="button"
            disabled={left === 0 || next === null || busy !== null}
            onClick={() => void run("next")}
            className="flex shrink-0 items-center gap-2.5 rounded font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-signal disabled:text-faint"
          >
            {busy === "next" ? (
              <Spinner className="h-4 w-4" />
            ) : (
              <Play aria-hidden className="h-4 w-4 text-faint" />
            )}
            Fill next
          </button>
          <input
            id={inputId}
            type="number"
            min={1}
            max={left > 0 ? left : undefined}
            step={1}
            value={rowsText}
            disabled={left === 0}
            onChange={(event) => setRowsText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void run("next");
              }
            }}
            aria-label="How many rows to fill next"
            aria-invalid={next === null || undefined}
            className={cn(
              "w-16 rounded-md bg-surface px-2 py-0.5 text-sm tabular-nums text-foreground ring-1 ring-inset outline-none focus:ring-2 disabled:bg-wash disabled:text-faint",
              next === null ? "ring-danger-edge focus:ring-danger-edge" : "ring-edge focus:ring-signal",
            )}
          />
          <label htmlFor={inputId} className={left > 0 ? "text-muted" : "text-faint"}>
            rows
          </label>
          <CornerDownLeft aria-hidden className="ml-auto h-3.5 w-3.5 text-faint" />
        </div>
        <button
          type="button"
          disabled={left === 0 || busy !== null}
          onClick={() => void run("all")}
          className={cn(
            row,
            "font-medium text-foreground outline-none hover:bg-wash focus-visible:bg-wash disabled:text-faint disabled:hover:bg-transparent",
          )}
        >
          {busy === "all" ? <Spinner className="h-4 w-4" /> : <FastForward aria-hidden className="h-4 w-4 text-faint" />}
          Fill all remaining
          <span className="ml-auto font-normal tabular-nums text-muted">{left > 0 ? rows(left) : "Nothing left"}</span>
        </button>
      </div>
      {refusal && (
        <div className="border-t border-hairline px-3 py-2">
          <ErrorMessage message={refusal} />
        </div>
      )}
    </div>
  );
}
