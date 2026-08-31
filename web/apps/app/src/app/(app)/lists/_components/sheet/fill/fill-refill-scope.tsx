"use client";

import { useState } from "react";
import { Button, Input } from "@bower/ui";
import { DEFAULT_SCOPE_ROWS, parseScopeRows } from "./lib/fill-scope";

/** The tracker popover's scoped continue: refill the column's next N
 * unanswered rows, or all of them. N is an upper bound, not a promise
 * (the server excludes answered rows and admits the true eligible
 * count), so the copy says "next" and never claims a total. Renders
 * only beside a terminal run: one run per column is the invariant, so
 * a refill during a live walk could only be refused. A refusal
 * renders verbatim below (tier 1: the server wrote it). */
export function RefillScope({ onRefill }: { onRefill: (rows?: number) => Promise<string | null> }) {
  const [rowsText, setRowsText] = useState(String(DEFAULT_SCOPE_ROWS));
  const [busy, setBusy] = useState<"next" | "all" | null>(null);
  const [refusal, setRefusal] = useState("");
  const rows = parseScopeRows(rowsText);

  async function run(which: "next" | "all") {
    if (busy) return;
    setBusy(which);
    setRefusal("");
    const message = await onRefill(which === "next" ? (rows ?? undefined) : undefined);
    setBusy(null);
    if (message) setRefusal(message);
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" loading={busy === "next"} disabled={rows === null} onClick={() => void run("next")}>
          Fill next
        </Button>
        <Input
          type="number"
          min={1}
          step={1}
          value={rowsText}
          onChange={(event) => setRowsText(event.target.value)}
          invalid={rows === null}
          aria-label="How many rows to fill next"
          className="w-20 py-1 text-xs"
        />
        <span className="text-xs text-muted">rows</span>
        <Button size="sm" variant="ghost" loading={busy === "all"} onClick={() => void run("all")}>
          Fill all remaining
        </Button>
      </div>
      {refusal && <p className="max-w-md text-xs text-danger">{refusal}</p>}
    </div>
  );
}
