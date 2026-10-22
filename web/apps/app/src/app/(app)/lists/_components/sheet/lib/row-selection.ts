import type { RowSelectionBody } from "@bower/api";

/** The rows a person has picked, in one of two shapes: some rows by
 * id, or every row of the sheet but the ones to keep (unticked after
 * a select all); `keep` is the wire's word and the service's. The second shape is what lets one click take a sheet the page
 * has only partly loaded: the server reads "all" under its own lock,
 * and the page only ever names rows it holds. Pure; the hook that
 * owns the state calls these. */
export type RowSelection =
  | { mode: "some"; ids: ReadonlySet<string> }
  | { mode: "all"; keep: ReadonlySet<string> };

export const NONE: RowSelection = { mode: "some", ids: new Set() };

export function everything(): RowSelection {
  return { mode: "all", keep: new Set() };
}

export function isSelected(selection: RowSelection, id: string): boolean {
  return selection.mode === "some" ? selection.ids.has(id) : !selection.keep.has(id);
}

/** Flip one row. Under "all", ticking a row back takes it out of the
 * exceptions; unticking adds it. */
export function toggled(selection: RowSelection, id: string): RowSelection {
  if (selection.mode === "some") {
    const ids = new Set(selection.ids);
    if (ids.has(id)) ids.delete(id);
    else ids.add(id);
    return { mode: "some", ids };
  }
  const keep = new Set(selection.keep);
  if (keep.has(id)) keep.delete(id);
  else keep.add(id);
  return { mode: "all", keep };
}

/** Add rows (a shift-click range): never removes. */
export function withRows(selection: RowSelection, ids: Iterable<string>): RowSelection {
  if (selection.mode === "some") {
    const next = new Set(selection.ids);
    for (const id of ids) next.add(id);
    return { mode: "some", ids: next };
  }
  const keep = new Set(selection.keep);
  for (const id of ids) keep.delete(id);
  return { mode: "all", keep };
}

/** The selection with ids the page no longer holds dropped: a row
 * deleted elsewhere or gone on a re-read must not keep counting. The
 * same object comes back when nothing changed, so a state set from it
 * is a no-op. */
export function pruned(selection: RowSelection, live: ReadonlySet<string>): RowSelection {
  const kept = selection.mode === "some" ? selection.ids : selection.keep;
  const next = new Set([...kept].filter((id) => live.has(id)));
  if (next.size === kept.size) return selection;
  return selection.mode === "some" ? { mode: "some", ids: next } : { mode: "all", keep: next };
}

/** The selection as the sheet sees it: pruned to the rows the page
 * holds, and a selection that names nothing is NONE whatever its
 * mode. Under "all", a mode that outlives its last row would tick
 * every row added next, and a select all on an empty sheet would
 * do the same. */
export function settled(selection: RowSelection, live: ReadonlySet<string>, rowCount: number): RowSelection {
  const kept = pruned(selection, live);
  if (kept.mode === "all" && count(kept, rowCount) === 0) return NONE;
  return kept;
}

/** How many rows the selection names, given the sheet's count. */
export function count(selection: RowSelection, rowCount: number): number {
  return selection.mode === "some" ? selection.ids.size : Math.max(rowCount - selection.keep.size, 0);
}

export function isEmpty(selection: RowSelection, rowCount: number): boolean {
  return count(selection, rowCount) === 0;
}

/** The select-all box's state: every row, some rows, or none. */
export function headerState(selection: RowSelection, rowCount: number): "none" | "some" | "all" {
  const n = count(selection, rowCount);
  if (n === 0) return "none";
  return n === rowCount ? "all" : "some";
}

export function toBody(selection: RowSelection): RowSelectionBody {
  return selection.mode === "some" ? { row_ids: [...selection.ids] } : { all: true, keep: [...selection.keep] };
}
