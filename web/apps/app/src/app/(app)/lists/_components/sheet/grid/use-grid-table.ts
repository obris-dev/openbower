"use client";

import { useMemo } from "react";
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

/** The headless table model over the sheet's columns and loaded rows:
 * one table column per sheet column IN THE SHEET'S ORDER (the order is
 * the server's and a reorder goes through the reorder endpoint, so
 * the table never holds an order of its own), with the per-column
 * width state the caller owns. The ONE place the grid knows its table
 * library: everything else reads headers and widths off what this
 * returns. */
export function useGridTable({
  columns,
  rows,
  sizing,
  sizingInfo,
  onSizingChange,
  onSizingInfoChange,
}: {
  columns: ListColumn[];
  rows: RenderableListRow[];
  sizing: ColumnSizingState;
  sizingInfo: ColumnSizingInfoState;
  onSizingChange: OnChangeFn<ColumnSizingState>;
  onSizingInfoChange: OnChangeFn<ColumnSizingInfoState>;
}): { headers: GridHeader[]; totalWidth: number } {
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
  return { headers, totalWidth: table.getTotalSize() };
}
