"use client";

import { useCallback, useMemo } from "react";
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnSizingInfoState,
  type ColumnSizingState,
  type Header,
  type OnChangeFn,
  type RowData,
} from "@tanstack/react-table";
import type { ListColumn, RenderableListRow } from "@bower/api";

import { DEFAULT_COLUMN_WIDTH, MAX_COLUMN_WIDTH, MIN_COLUMN_WIDTH } from "./lib/column-sizing";
import { pendingWidth } from "./lib/column-widths";

declare module "@tanstack/react-table" {
  // The sheet's own column record rides every table column, so a
  // header or a cell reaches it off the model it was handed instead
  // of looking it up by key in a parallel array.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    column: ListColumn;
  }
}

export type GridHeader = Header<RenderableListRow, unknown>;

/** What a cell's `width` style takes: the model's number once the
 * stored widths are in state, the pre-paint property until then. */
export type CellWidth = number | string;

/** The headless table model over the sheet's columns and loaded rows:
 * one table column per sheet column IN THE SHEET'S ORDER (the order is
 * the server's and a reorder goes through the reorder endpoint, so
 * the table never holds an order of its own), with the per-column
 * width state the caller owns. The ONE place the grid knows its table
 * library: everything else reads headers and widths off what this
 * returns, widths through `widthOf`, which hands out the pre-paint
 * property until the stored widths are loaded. */
export function useGridTable({
  listId,
  columns,
  rows,
  widthsLoaded,
  sizing,
  sizingInfo,
  onSizingChange,
  onSizingInfoChange,
}: {
  listId: string;
  columns: ListColumn[];
  rows: RenderableListRow[];
  widthsLoaded: boolean;
  sizing: ColumnSizingState;
  sizingInfo: ColumnSizingInfoState;
  onSizingChange: OnChangeFn<ColumnSizingState>;
  onSizingInfoChange: OnChangeFn<ColumnSizingInfoState>;
}): { headers: GridHeader[]; totalWidth: number; widthOf: (header: GridHeader) => CellWidth } {
  const defs = useMemo<ColumnDef<RenderableListRow>[]>(
    () =>
      columns.map((column) => ({
        id: column.key,
        accessorFn: (row) => row.data[column.key] ?? "",
        meta: { column },
      })),
    [columns],
  );
  const table = useReactTable({
    data: rows,
    columns: defs,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: "onChange",
    defaultColumn: { size: DEFAULT_COLUMN_WIDTH, minSize: MIN_COLUMN_WIDTH, maxSize: MAX_COLUMN_WIDTH },
    state: { columnSizing: sizing, columnSizingInfo: sizingInfo },
    onColumnSizingChange: onSizingChange,
    onColumnSizingInfoChange: onSizingInfoChange,
  });
  // Flat columns only, so the one header group is the whole header.
  const headers = table.getHeaderGroups()[0]?.headers ?? [];
  const widthOf = useCallback(
    (header: GridHeader): CellWidth => {
      const size = header.getSize();
      return widthsLoaded ? size : pendingWidth(listId, header.id, size);
    },
    [listId, widthsLoaded],
  );
  return { headers, totalWidth: table.getTotalSize(), widthOf };
}
