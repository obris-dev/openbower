import type { RowSelectionBody } from "@bower/api";

/** The rows a person has picked, in one of two shapes: some rows by
 * id, or every row of the sheet but the ids unticked after a select
 * all. The second shape is what lets one click take a sheet the page
 * has only partly loaded: the server reads "all" under its own lock,
 * and the page only ever names rows it holds. Pure; the hook that
 * owns the state calls these. */
export type RowSelection =
  | { mode: "some"; ids: ReadonlySet<string> }
  | { mode: "all"; except: ReadonlySet<string> };

/** What the grid needs to draw and drive a selection: the sheet owns
 * the state, the grid's gutter cells and header box read and call. */
export type RowSelectionControls = {
  isSelected: (rowId: string) => boolean;
  /** Any row picked: every gutter then shows its box, not its number. */
  selecting: boolean;
  headerState: "none" | "some" | "all";
  onToggle: (rowId: string, range: boolean) => void;
  onToggleAll: () => void;
};

export const NONE: RowSelection = { mode: "some", ids: new Set() };

export function everything(): RowSelection {
  return { mode: "all", except: new Set() };
}

export function isSelected(selection: RowSelection, id: string): boolean {
  return selection.mode === "some" ? selection.ids.has(id) : !selection.except.has(id);
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
  const except = new Set(selection.except);
  if (except.has(id)) except.delete(id);
  else except.add(id);
  return { mode: "all", except };
}

/** Add rows (a shift-click range): never removes. */
export function withRows(selection: RowSelection, ids: Iterable<string>): RowSelection {
  if (selection.mode === "some") {
    const next = new Set(selection.ids);
    for (const id of ids) next.add(id);
    return { mode: "some", ids: next };
  }
  const except = new Set(selection.except);
  for (const id of ids) except.delete(id);
  return { mode: "all", except };
}

/** The selection with ids the page no longer holds dropped: a row
 * deleted elsewhere or gone on a re-read must not keep counting. The
 * same object comes back when nothing changed, so a state set from it
 * is a no-op. */
export function pruned(selection: RowSelection, live: ReadonlySet<string>): RowSelection {
  const kept = selection.mode === "some" ? selection.ids : selection.except;
  const next = new Set([...kept].filter((id) => live.has(id)));
  if (next.size === kept.size) return selection;
  return selection.mode === "some" ? { mode: "some", ids: next } : { mode: "all", except: next };
}

/** How many rows the selection names, given the sheet's count. */
export function count(selection: RowSelection, rowCount: number): number {
  return selection.mode === "some" ? selection.ids.size : Math.max(rowCount - selection.except.size, 0);
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
  return selection.mode === "some" ? { row_ids: [...selection.ids] } : { all: true, keep: [...selection.except] };
}
