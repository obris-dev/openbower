"use client";

import type { RefObject } from "react";
import type { RenderableListRow } from "@bower/api";
import type { VirtualItem } from "@tanstack/react-virtual";

import type { SearchProviderChoice } from "../fill";
import { GridRow } from "./grid-row";
import type { RowSelectionControls } from "./row-selection-controls";
import type { CellWidth, GridHeader } from "./use-grid-table";

/** The rows band: a box as tall as every loaded row would be, holding
 * only the rows the virtualizer says are near the viewport, each
 * placed at its own offset. A sheet with no rows says so in one line
 * across the grid; the header above still renders, since a column is
 * named there before it exists. */
export function Body({
  bodyRef,
  rows,
  items,
  totalSize,
  scrollMargin,
  headers,
  widthOf,
  searchProvider,
  selection,
  pendingColumn,
}: {
  bodyRef: RefObject<HTMLDivElement | null>;
  rows: RenderableListRow[];
  items: VirtualItem[];
  totalSize: number;
  scrollMargin: number;
  headers: GridHeader[];
  widthOf: (header: GridHeader) => CellWidth;
  searchProvider: SearchProviderChoice;
  selection?: RowSelectionControls;
  pendingColumn?: boolean;
}) {
  if (rows.length === 0) {
    return (
      <div ref={bodyRef} className="sticky left-0 p-6 text-sm text-muted">
        This sheet has no rows.
      </div>
    );
  }
  return (
    <div ref={bodyRef} className="relative w-max min-w-full" style={{ height: totalSize }}>
      {items.map((item) => {
        const row = rows[item.index];
        if (!row) return null;
        return (
          <GridRow
            key={row.id}
            row={row}
            index={item.index}
            headers={headers}
            widthOf={widthOf}
            offset={item.start - scrollMargin}
            searchProvider={searchProvider}
            selection={selection}
            pendingColumn={pendingColumn}
          />
        );
      })}
    </div>
  );
}
