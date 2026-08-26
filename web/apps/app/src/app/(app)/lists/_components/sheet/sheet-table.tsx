"use client";

import { DndContext, closestCenter, type DragEndEvent, type Modifier } from "@dnd-kit/core";
import { horizontalListSortingStrategy, SortableContext } from "@dnd-kit/sortable";
import { Plus } from "lucide-react";
import { useCallback, useRef } from "react";
import { Dropdown, DropdownButton, DropdownMenu } from "@bower/ui";
import { isNumericColumn, type ColumnFillSummary, type FillWire, type ListColumn, type RenderableListRow } from "@bower/api";

import { cellHref, cellLinkIsExternal } from "../../../_components/cell-link";
import { AddColumnMenuItems, type ColumnKind } from "./add-column";
import { ColumnHeader, useColumnSensors } from "./column-header";
import { clampDragX } from "./lib/drag-bounds";
import { orderAfterDrag } from "./lib/drag-order";
import { AiCellState, FillTrackerCell } from "./fill";

/** The tracker row's inputs, one object because they only travel
 * together: the exposed jobs and the management verbs the popover's
 * chip and scoped continue call. */
export type SheetFills = {
  // The sheet's own id: the popover's prompt edit calls the
  // column-scoped endpoint.
  listId: string;
  jobs: FillWire[];
  // Server truth per column (current job, canonical filled count).
  summaries: ColumnFillSummary[];
  rowCount: number;
  onStop: (jobId: string) => Promise<string | null>;
  onRefill: (job: FillWire | null, columnKey: string, opts?: { rows?: number; resume?: boolean }) => Promise<string | null>;
};

function Cell({ column, value }: { column: ListColumn; value: string }) {
  if (!value) return null;
  const href = cellHref(column.type, value);
  if (href) {
    return (
      <a
        href={href}
        {...(cellLinkIsExternal(column.type) ? { target: "_blank", rel: "noreferrer" } : {})}
        className="text-muted hover:text-signal"
      >
        {value}
      </a>
    );
  }
  return <span className="text-foreground">{value}</span>;
}

/** The rows table: columns straight from the sheet's schema (types
 * drive rendering only). The PARENT owns the scroll region (the grid
 * band of the full-bleed layout); the header row sticks to that
 * nearest scrolling ancestor. AI cell states ride the ROW itself
 * (RenderableListRow.states), so a value and its state can never come from
 * different requests and disagree; `fills`
 * adds the tracker row under the header (one cell per AI column, the
 * per-column fill surface; the footer tray stays the sheet-wide one).
 * `onAddColumn` puts the spreadsheet-native "+" entry point in the
 * last header cell (header only, never the body rows). */
export function SheetTable({
  columns,
  rows,
  fills,
  onAddColumn,
  onReorder,
}: {
  columns: ListColumn[];
  rows: RenderableListRow[];
  /** Absent on a sheet that cannot be reordered; its presence is what
   * arms both the drag and the header menu. */
  onReorder?: (keys: string[]) => void;
  fills?: SheetFills;
  onAddColumn?: (kind: ColumnKind) => void;
}) {
  // Hooks before any early return: the empty-sheet branch below is a
  // render path like any other.
  const sensors = useColumnSensors();
  const tableRef = useRef<HTMLTableElement>(null);

  // The table is the bound, so a column cannot be carried off the
  // sheet and left somewhere it has no meaning. The table's rect
  // spans the "#" gutter and the trailing "+" as well, so a header
  // still travels over both; what this stops is the unbounded case,
  // not travel within the sheet. Auto scroll keeps working, since
  // that rect covers the whole sheet and not just its visible part.
  // y is pinned because a column reorder is horizontal by definition:
  // lifting one out of its row says nothing about where it lands.
  const boundToTable = useCallback<Modifier>(({ transform, draggingNodeRect }) => {
    const bounds = tableRef.current?.getBoundingClientRect();
    // No rect yet means no bound to apply, never a bound of zero.
    if (!draggingNodeRect || !bounds) return { ...transform, y: 0 };
    return { ...transform, x: clampDragX(transform.x, draggingNodeRect, bounds), y: 0 };
  }, []);

  function onDragEnd(event: DragEndEvent) {
    const keys = orderAfterDrag(columns, event);
    if (keys) onReorder?.(keys);
  }

  if (rows.length === 0) {
    return <p className="p-6 text-sm text-muted">This sheet has no rows.</p>;
  }
  // The tracker row exists only once an AI column does: a sheet of
  // plain columns has no fill state to track, and an all-empty row
  // would be dead height between header and rows.
  const tracker = fills !== undefined && columns.some((column) => column.fill !== null) ? fills : undefined;

  return (
    // closestCenter over a horizontal strip: the pointer sits inside
    // one header cell at a time, so the nearest centre IS the column
    // being displaced.
    <DndContext sensors={sensors} collisionDetection={closestCenter} modifiers={[boundToTable]} onDragEnd={onDragEnd}>
      <table ref={tableRef} className="w-full text-left text-sm">
      <thead>
        {/* The pinned h-11 is the contract that makes the tracker
            row's top-11 exact: both head rows stick as one unit, and
            an auto header height would drift under the "+" cell's
            padding or a font swap. */}
        <tr className="group/header h-11 text-xs uppercase tracking-wide text-faint">
          <th className="sticky top-0 bg-surface px-4 py-3 text-right font-medium">#</th>
          <SortableContext items={columns.map((column) => column.key)} strategy={horizontalListSortingStrategy}>
            {columns.map((column) => (
              <ColumnHeader key={column.key} column={column} columns={columns} onReorder={onReorder} />
            ))}
          </SortableContext>
          {onAddColumn && (
            <th className="sticky top-0 w-10 bg-surface px-2 py-2">
              {/* The same kind menu as the toolbar primary: one
                  gesture vocabulary from both entry points. */}
              <Dropdown>
                <DropdownButton
                  aria-label="Add column"
                  className="rounded-md p-1.5 text-faint hover:bg-wash hover:text-foreground"
                >
                  <Plus aria-hidden className="h-4 w-4" />
                </DropdownButton>
                <DropdownMenu anchor="bottom end">
                  <AddColumnMenuItems onPick={onAddColumn} />
                </DropdownMenu>
              </Dropdown>
            </th>
          )}
        </tr>
        {tracker && (
          // The tracker row, part of the table so it scrolls
          // horizontally WITH the columns it describes: one compact
          // fill-state cell per AI column (the click-in management
          // popover behind it), empty cells elsewhere.
          <tr className="text-xs">
            <td className="sticky top-11 bg-surface" />
            {columns.map((column) => (
              <td key={column.key} className="sticky top-11 bg-surface px-3 pb-2">
                {column.fill !== null && (
                  <FillTrackerCell
                    listId={tracker.listId}
                    column={column}
                    summary={tracker.summaries.find((entry) => entry.column_key === column.key)}
                    jobs={tracker.jobs}
                    rowCount={tracker.rowCount}
                    onStop={tracker.onStop}
                    onRefill={tracker.onRefill}
                  />
                )}
              </td>
            ))}
            {onAddColumn && <td className="sticky top-11 bg-surface" />}
          </tr>
        )}
      </thead>
      <tbody className="divide-y divide-hairline">
        {rows.map((row) => (
          <tr key={row.id} className="align-top">
            <td className="px-4 py-2.5 text-right tabular-nums text-faint">{row.position}</td>
            {columns.map((column) => {
              // A state dresses only AI cells; without one, a value
              // is the plain filled cell and no value is
              // not-attempted, undecorated by design.
              // A REAL VALUE always outranks a state. Both come off
              // THIS row object, so they are one encoding rather than
              // two reads that can disagree.
              const value = row.data[column.key] ?? "";
              const state = column.fill && !value ? row.states?.[column.key] : undefined;
              return (
                <td
                  key={column.key}
                  className={`px-4 py-2.5 ${isNumericColumn(column) ? "text-right tabular-nums" : ""}`}
                >
                  {state !== undefined ? (
                    // A state cell holds a short word, a dot, or a
                    // shimmer, nothing to truncate, and truncation's
                    // overflow-hidden would clip the focus tooltip.
                    <AiCellState state={state} />
                  ) : (
                    <div className="max-w-64 truncate">
                      <Cell column={column} value={value} />
                    </div>
                  )}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
      </table>
    </DndContext>
  );
}
