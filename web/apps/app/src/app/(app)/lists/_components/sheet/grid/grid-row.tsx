"use client";

import type { RenderableListRow } from "@bower/api";

import type { SearchProviderChoice } from "../fill";
import { GridCell } from "./grid-cell";
import { GutterCell, RowGutterCell } from "./gutter-cell";
import { ROW_HEIGHT, TRAILING_CELL_WIDTH } from "./lib/column-sizing";
import type { RowSelectionControls } from "./lib/row-selection";
import type { GridHeader } from "./use-grid-table";

/** One row of the body, positioned by the virtualizer: the number in
 * the gutter (counted by the caller, since the page's array IS the
 * sheet order and nothing about order rides the row), one cell per
 * column at that column's width, and the empty trailing cell under
 * the header's Add column, marked as the row's (a press on it is a
 * press on the row). The row is a hover group: its gutter swaps the
 * number for the row's checkbox while the pointer is anywhere on it. */
export function GridRow({
  row,
  index,
  headers,
  offset,
  searchProvider,
  selection,
}: {
  row: RenderableListRow;
  index: number;
  headers: GridHeader[];
  /** The row's top, relative to the body. */
  offset: number;
  searchProvider: SearchProviderChoice;
  /** Absent on a grid that cannot select: the gutter is the number alone. */
  selection?: RowSelectionControls;
}) {
  return (
    <div
      className="group absolute left-0 top-0 flex w-max min-w-full"
      style={{ height: ROW_HEIGHT, transform: `translateY(${offset}px)` }}
    >
      {selection ? (
        <RowGutterCell
          rowId={row.id}
          number={index + 1}
          selected={selection.isSelected(row.id)}
          selecting={selection.selecting}
          onToggle={(range) => selection.onToggle(row.id, range)}
        />
      ) : (
        <GutterCell className="border-b" rowId={row.id}>
          {index + 1}
        </GutterCell>
      )}
      {headers.map((header) => (
        <GridCell
          key={header.id}
          column={header.column.columnDef.meta!.column}
          row={row}
          width={header.getSize()}
          searchProvider={searchProvider}
        />
      ))}
      <div
        data-row-id={row.id}
        className="flex-none border-b border-r border-hairline"
        style={{ width: TRAILING_CELL_WIDTH }}
      />
    </div>
  );
}
