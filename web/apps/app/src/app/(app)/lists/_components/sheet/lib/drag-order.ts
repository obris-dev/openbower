import type { ListColumn } from "@bower/api";

import { moveColumn } from "./column-order.ts";

/** What a drag actually reports, narrowed to the two fields this
 * reads. dnd-kit's DragEndEvent satisfies it structurally, so the
 * caller passes one straight through while this module stays callable
 * without a renderer or a drag. */
export type DragLanding = {
  active: { id: string | number };
  over: { id: string | number } | null;
};

/** The key order after a drag, or null if it did not land anywhere
 * new. Null so a caller cannot send a request for a move that did not
 * happen: dropping a column back where it started is a no-op, not a
 * reorder, and the endpoint would answer 200 to a write of nothing. */
export function orderAfterDrag(columns: ListColumn[], event: DragLanding): string[] | null {
  const { active, over } = event;
  if (!over || active.id === over.id) return null;
  const from = columns.findIndex((column) => column.key === active.id);
  const to = columns.findIndex((column) => column.key === over.id);
  // A key neither side recognises means the sheet moved under the
  // gesture; refusing beats guessing which half was meant.
  if (from < 0 || to < 0) return null;
  return moveColumn(columns, from, to);
}
