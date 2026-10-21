"use client";

import { DndContext, closestCenter, type DragEndEvent, type Modifier } from "@dnd-kit/core";
import { useCallback, useRef, type RefObject } from "react";
import type { ColumnType, ListColumn, RenderableListRow, WebhookColumn } from "@bower/api";

import type { AddColumnKind } from "../add-column";
import type { SearchProviderChoice } from "../fill";
import type { ColumnOutcome } from "../use-columns";
import { Body } from "./body";
import { HeaderRow } from "./header-row";
import { clampDragX } from "./lib/drag-bounds";
import { orderAfterDrag } from "./lib/drag-order";
import { TrackerRow, type SheetFills } from "./tracker-row";
import { useColumnSensors } from "./use-column-sensors";
import { useColumnWidths } from "./use-column-widths";
import { useGridTable } from "./use-grid-table";
import { useVirtualRows } from "./use-virtual-rows";

/** The sheet's grid: fixed-width columns straight from the sheet's
 * schema (types drive rendering only), rows at one fixed height,
 * everything left aligned. The PARENT owns the scroll region (the
 * grid band of the full-bleed layout) and hands it down, so the
 * header sticks to its top, the gutter to its left, and the
 * virtualizer paints only the rows near its viewport. Column widths
 * are this browser's memory per sheet; the column ORDER is the
 * server's, and a drag asks for a reorder rather than holding one.
 * AI cell states ride the ROW itself (RenderableListRow.states), so a
 * value and its state can never come from different requests and
 * disagree; `fills` adds the tracker row under the header (one cell
 * per AI column, THE per-column fill surface; the footer keeps only a
 * passive glance). */
export function SheetGrid({
  listId,
  scrollRef,
  columns,
  rows,
  fills,
  onAddColumn,
  onReorder,
  onRenameColumn,
  onDeleteColumn,
  onEditWebhook,
  pendingColumn,
  onNamePending,
  searchProvider = null,
}: {
  listId: string;
  /** The scroll region the grid lives in; the virtualizer listens to it. */
  scrollRef: RefObject<HTMLDivElement | null>;
  columns: ListColumn[];
  rows: RenderableListRow[];
  /** The deployment's web-search provider once the sheet has fetched it
   * (a rate-limited cell's popover composes the paid-provider nudge). */
  searchProvider?: SearchProviderChoice;
  /** Absent on a sheet that cannot be reordered; its presence is what
   * arms both the drag and the header menu's moves. */
  onReorder?: (keys: string[]) => void;
  onRenameColumn?: (key: string, label: string) => void;
  /** A plain column being named before it exists; null when none is. */
  pendingColumn?: { type: ColumnType } | null;
  onNamePending?: (label: string) => void;
  /** Resolves with the outcome, so the confirm tier that asked can
   * render a refusal (a column a webhook waits on) in place. */
  onDeleteColumn?: (column: ListColumn) => Promise<ColumnOutcome>;
  /** Reopens the Send webhook drawer on an existing webhook column. */
  onEditWebhook?: (column: WebhookColumn) => void;
  fills?: SheetFills;
  onAddColumn?: (kind: AddColumnKind) => void;
}) {
  const sensors = useColumnSensors();
  const gridRef = useRef<HTMLDivElement>(null);
  const { sizing, sizingInfo, onSizingChange, onSizingInfoChange } = useColumnWidths(
    listId,
    columns.map((column) => column.key),
  );
  const { headers } = useGridTable({ columns, rows, sizing, sizingInfo, onSizingChange, onSizingInfoChange });
  const { bodyRef, items, totalSize, scrollMargin } = useVirtualRows(rows.length, scrollRef);

  // The grid is the bound, so a column cannot be carried off the
  // sheet and left somewhere it has no meaning. The grid's rect
  // spans the gutter and the trailing cell as well, so a header
  // still travels over both; what this stops is the unbounded case,
  // not travel within the sheet. Auto scroll keeps working, since
  // that rect covers the whole grid and not just its visible part.
  // y is pinned because a column reorder is horizontal by definition:
  // lifting one out of its row says nothing about where it lands.
  const boundToGrid = useCallback<Modifier>(({ transform, draggingNodeRect }) => {
    const bounds = gridRef.current?.getBoundingClientRect();
    // No rect yet means no bound to apply, never a bound of zero.
    if (!draggingNodeRect || !bounds) return { ...transform, y: 0 };
    return { ...transform, x: clampDragX(transform.x, draggingNodeRect, bounds), y: 0 };
  }, []);

  function onDragEnd(event: DragEndEvent) {
    const keys = orderAfterDrag(columns, event);
    if (keys) onReorder?.(keys);
  }

  // The tracker row exists only once an AI column does: a sheet of
  // plain columns has no fill state to track, and an all-empty row
  // would be dead height between header and rows.
  const tracker = fills !== undefined && columns.some((column) => column.kind === "ai") ? fills : undefined;

  return (
    // closestCenter over a horizontal strip: the pointer sits inside
    // one header cell at a time, so the nearest centre IS the column
    // being displaced.
    <DndContext sensors={sensors} collisionDetection={closestCenter} modifiers={[boundToGrid]} onDragEnd={onDragEnd}>
      {/* overflow-clip (never hidden, which would make this the scroll
          container the sticky bands look for): the resize guide runs
          down the grid and must stop at its bottom edge rather than
          stretch the scroll region's extent under the pointer. */}
      <div ref={gridRef} className="relative isolate w-max min-w-full overflow-clip text-sm">
        <HeaderRow
          headers={headers}
          columns={columns}
          onReorder={onReorder}
          onRenameColumn={onRenameColumn}
          onDeleteColumn={onDeleteColumn}
          onEditWebhook={onEditWebhook}
          pendingColumn={pendingColumn}
          onNamePending={onNamePending}
          onAddColumn={onAddColumn}
        />
        {tracker && <TrackerRow headers={headers} fills={tracker} />}
        <Body
          bodyRef={bodyRef}
          rows={rows}
          items={items}
          totalSize={totalSize}
          scrollMargin={scrollMargin}
          headers={headers}
          searchProvider={searchProvider}
        />
      </div>
    </DndContext>
  );
}
