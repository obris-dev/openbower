"use client";

import { useEffect, useState } from "react";
import { Button, ConfirmDialog, cn } from "@bower/ui";

import { ConfirmDelete } from "../../../_components/confirm-delete";
import type { RowsOutcome } from "./use-rows";

/** The bar that floats over the grid while rows are picked: how many,
 * Deselect, and Delete rows, which asks in the one dialog: the pill
 * is too small to hold the question, a consequence and two answers,
 * and it floats over the rows it names, so growing it under the
 * pointer at the moment of the decision would be worse than the
 * overlay (LAYOUT.md rule 6 names this as the modal's second act,
 * beside the row menu's delete). Mounted whatever
 * the count, so the dialog outlives the selection it acted on: the
 * bar hides once nothing is picked, the dialog closes on its own
 * clock, and `onEmptied` runs once the selection has emptied through
 * this bar (a delete, once the dialog has closed; a deselect at once),
 * for the caller to put focus somewhere the bar no longer is. */
export function SelectionBar({
  count,
  onDeselect,
  onDelete,
  onEmptied,
}: {
  count: number;
  onDeselect: () => void;
  onDelete: () => Promise<RowsOutcome>;
  onEmptied?: () => void;
}) {
  const [confirming, setConfirming] = useState<{ busy: boolean } | null>(null);
  const [closedAfterDelete, setClosedAfterDelete] = useState(false);
  useEffect(() => {
    if (!closedAfterDelete || confirming !== null) return;
    const frame = requestAnimationFrame(() => {
      setClosedAfterDelete(false);
      onEmptied?.();
    });
    return () => cancelAnimationFrame(frame);
  }, [closedAfterDelete, confirming, onEmptied]);

  const rows = count === 1 ? "1 row" : `${count.toLocaleString("en-US")} rows`;

  function confirmDelete() {
    void (async () => {
      setConfirming({ busy: true });
      const outcome = await onDelete();
      // Gone or refused, the question is answered either way: a
      // refusal is the sheet's toast, and the rows stay as they were.
      setConfirming(null);
      if (outcome.ok) setClosedAfterDelete(true);
    })();
  }

  return (
    <>
      {/* Always in the tree, shown by the count: the pill fades and
          lifts 4px in as the first row is picked and out as the last
          is unpicked, instead of popping. Hidden, it is inert: no
          pointer, no focus, out of the accessibility tree. */}
      <div
        inert={count === 0}
        className={cn(
          "pointer-events-none absolute inset-x-0 bottom-4 flex justify-center transition duration-100 ease-out motion-reduce:transition-none",
          count === 0 ? "translate-y-1 opacity-0" : "translate-y-0 opacity-100",
        )}
      >
        <div
          role="toolbar"
          aria-label={`${rows} selected`}
          className={cn(
            "flex items-center gap-2 rounded-xl bg-surface/95 py-1.5 pl-3 pr-1.5 shadow-lg ring-1 ring-hairline backdrop-blur dark:ring-foreground/15",
            count > 0 && "pointer-events-auto",
          )}
        >
          <p className="whitespace-nowrap text-sm text-muted">
            <span className="font-medium tabular-nums text-foreground">{count.toLocaleString("en-US")}</span>
            {count === 1 ? " row selected" : " rows selected"}
          </p>
          <span aria-hidden className="h-5 w-px shrink-0 bg-hairline" />
          <Button
            size="sm"
            variant="ghost"
            className="font-medium text-muted hover:text-foreground"
            onClick={() => {
              onDeselect();
              onEmptied?.();
            }}
          >
            Deselect
          </Button>
          <Button size="sm" variant="danger" onClick={() => setConfirming({ busy: false })}>
            Delete rows
          </Button>
        </div>
      </div>
      <ConfirmDialog open={confirming !== null} onClose={() => !confirming?.busy && setConfirming(null)}>
        <ConfirmDelete
          layout="dialog"
          question={`Delete ${rows}?`}
          consequence={
            count === 1 ? "Are you sure you want to delete this row?" : "Are you sure you want to delete these rows?"
          }
          busy={confirming?.busy}
          onCancel={() => !confirming?.busy && setConfirming(null)}
          onDelete={confirmDelete}
        />
      </ConfirmDialog>
    </>
  );
}
