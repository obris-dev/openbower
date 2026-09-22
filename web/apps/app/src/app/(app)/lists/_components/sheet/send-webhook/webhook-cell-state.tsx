"use client";

import type { RenderableCellStateWire } from "@bower/api";

import { CauseMark } from "../fill";
import { CELL_FAILED, CELL_FAILED_CAUSE, CELL_FAILED_FACT, CELL_SENT, CELL_WAITING } from "./copy";

/** A Send webhook column's cell: the column holds no value, so the
 * state IS the cell, in the settled-word idiom of an AI cell (quiet,
 * faint, never louder than a real value). It reads off the same
 * ledger entry an AI cell does: `pending` (a send owed or retrying)
 * says waiting, `sent` and `failed` say themselves. A failed send
 * keeps the same quiet word but opens the fuller sentence on tap,
 * click, and focus (the CauseMark floor), pointing at the delivery
 * log, since the error itself is not on this wire. No entry (the row
 * has never been due for this column) renders nothing, and so does a
 * state this cell has no word for. */
export function WebhookCellState({ entry }: { entry: RenderableCellStateWire | undefined }) {
  if (entry === undefined) return null;
  if (entry.state === "failed") {
    return (
      <CauseMark cause={CELL_FAILED_CAUSE} fact={CELL_FAILED_FACT}>
        <span aria-hidden className="text-xs text-warning">
          {CELL_FAILED}
        </span>
      </CauseMark>
    );
  }
  if (entry.state === "sent") return <span className="text-xs text-faint">{CELL_SENT}</span>;
  if (entry.state === "pending") return <span className="text-xs text-faint">{CELL_WAITING}</span>;
  return null;
}
