"use client";

import { useEffect, useState } from "react";
import { Button, ConfirmDialog } from "@bower/ui";

import { ConfirmDelete } from "../../../_components/confirm-delete";
import type { RowsOutcome } from "./use-rows";

/** The bar that floats over the grid while rows are picked: how many,
 * Deselect, and Delete rows, which asks in the one dialog (the
 * selection has no slot of its own to confirm in). Mounted whatever
 * the count, so the dialog outlives the selection it acted on: the
 * bar hides once nothing is picked, the dialog closes on its own
 * clock, and `onDeleted` runs after it has closed, for the caller to
 * put focus somewhere the bar no longer is. */
export function SelectionBar({
  count,
  onDeselect,
  onDelete,
  onDeleted,
}: {
  count: number;
  onDeselect: () => void;
  onDelete: () => Promise<RowsOutcome>;
  onDeleted?: () => void;
}) {
  const [confirming, setConfirming] = useState<{ busy: boolean } | null>(null);
  const [closedAfterDelete, setClosedAfterDelete] = useState(false);
  useEffect(() => {
    if (!closedAfterDelete || confirming !== null) return;
    const frame = requestAnimationFrame(() => {
      setClosedAfterDelete(false);
      onDeleted?.();
    });
    return () => cancelAnimationFrame(frame);
  }, [closedAfterDelete, confirming, onDeleted]);

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
      {count > 0 && (
        <div className="pointer-events-none absolute inset-x-0 bottom-4 flex justify-center">
          <div
            role="toolbar"
            aria-label="Selected rows"
            className="pointer-events-auto flex items-center gap-3 rounded-lg border border-hairline bg-surface px-4 py-2 shadow-lg"
          >
            <p className="text-sm text-foreground">{rows} selected</p>
            <Button size="sm" variant="ghost" onClick={onDeselect}>
              Deselect
            </Button>
            <Button size="sm" variant="danger" onClick={() => setConfirming({ busy: false })}>
              Delete rows
            </Button>
          </div>
        </div>
      )}
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
