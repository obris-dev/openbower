"use client";

import { CircleHelp } from "lucide-react";

import { UNCONFIRMED_LABEL } from "../cell-details";
import { useCellActions } from "./cell-actions";

/** An unverified cell's answer, the one its run scored under the
 * floor: shown faint beside a faint (?), never as the cell's value and
 * never a link. A press opens the cell's details, where the answer is
 * read in full with how sure the model was, and used. */
export function UnconfirmedAnswer({ rowId, columnKey, value }: { rowId: string; columnKey: string; value: string }) {
  const actions = useCellActions();
  return (
    <button
      type="button"
      onClick={() => actions?.openDetails(rowId, columnKey)}
      aria-label={`${UNCONFIRMED_LABEL}: ${value}`}
      className="-mx-1.5 flex h-5 min-w-0 items-center gap-1.5 rounded px-1.5 outline-none hover:bg-wash focus-visible:ring-2 focus-visible:ring-signal"
    >
      <span className="truncate text-faint">{value}</span>
      <CircleHelp aria-hidden className="h-3.5 w-3.5 flex-none text-faint" />
    </button>
  );
}
