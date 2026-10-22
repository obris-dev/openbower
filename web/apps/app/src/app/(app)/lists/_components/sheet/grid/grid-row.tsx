"use client";

import { cn } from "@bower/ui";
import type { RenderableListRow } from "@bower/api";

import type { SearchProviderChoice } from "../fill";
import { GridCell } from "./grid-cell";
import { GutterCell, RowGutterCell } from "./gutter-cell";
import { DEFAULT_COLUMN_WIDTH, ROW_HEIGHT, TRAILING_CELL_WIDTH } from "./lib/column-sizing";
import type { RowSelectionControls } from "./row-selection-controls";
import type { CellWidth, GridHeader } from "./use-grid-table";

/** One row of the body, positioned by the virtualizer: the number in
 * the gutter (counted by the caller, since the page's array IS the
 * sheet order and nothing about order rides the row), one cell per
 * column at that column's width, the empty cell of a column being
 * named in the header (so the name field sits over a column and not
 * past the rows' edge), and the empty trailing cell under the header's
 * Add column, both marked as the row's (a press on either is a press
 * on the row). The row is a hover group: its gutter swaps the number
 * for the row's checkbox while the pointer is anywhere on it. */
export function GridRow({
  row,
  index,
  headers,
  widthOf,
  offset,
  searchProvider,
  selection,
  pendingColumn = false,
}: {
  row: RenderableListRow;
  index: number;
  headers: GridHeader[];
  widthOf: (header: GridHeader) => CellWidth;
  /** The row's top, relative to the body. */
  offset: number;
  searchProvider: SearchProviderChoice;
  /** Absent on a grid that cannot select: the gutter is the number alone. */
  selection?: RowSelectionControls;
  /** Whether the header holds a column being named, whose cells are
   * empty at the default width until it exists. */
  pendingColumn?: boolean;
}) {
  return (
    <div
      className={cn(
        "group absolute left-0 top-0 flex w-max min-w-full",
        // A picked row wears a faint wash in the selection's colour, so
        // the boxes, the rows and the bar read as one thing.
        selection?.isSelected(row.id) && "bg-signal/5",
      )}
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
          width={widthOf(header)}
          searchProvider={searchProvider}
        />
      ))}
      {pendingColumn && (
        <div
          data-row-id={row.id}
          className="flex-none border-b border-r border-hairline"
          style={{ width: DEFAULT_COLUMN_WIDTH }}
        />
      )}
      <div
        data-row-id={row.id}
        className="flex-none border-b border-r border-hairline"
        style={{ width: TRAILING_CELL_WIDTH }}
      />
    </div>
  );
}
