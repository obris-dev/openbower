"use client";

import { DndContext, closestCenter, type DragEndEvent, type Modifier } from "@dnd-kit/core";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { ConfirmDialog } from "@bower/ui";
import type { ColumnType, ListColumn, RenderableListRow, WebhookColumn } from "@bower/api";

import { ConfirmDelete } from "../../../../_components/confirm-delete";
import type { AddColumnKind } from "../add-column";
import type { SearchProviderChoice } from "../fill";
import type { ColumnOutcome } from "../use-columns";
import type { RowOutcome } from "../use-rows";
import { Body } from "./body";
import { CellActionsContext, type CellActions, type WriteHow } from "./cell-actions";
import { CellEditingContext, type CellEditing, type EditingCell } from "./cell-editing";
import { focusCell } from "./focus-cell";
import { HeaderRow } from "./header-row";
import { copiedValueFor } from "./lib/cell-clipboard";
import { neighbour, type Direction } from "./lib/cell-navigation";
import type { RowSelectionControls } from "./lib/row-selection";
import { clampDragX } from "./lib/drag-bounds";
import { orderAfterDrag } from "./lib/drag-order";
import { RowContextMenu } from "./row-context-menu";
import { TrackerRow, type SheetFills } from "./tracker-row";
import { useCellClipboard } from "./use-cell-clipboard";
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
  onSetCells,
  onRemoveRow,
  selection,
  searchProvider = null,
}: {
  listId: string;
  /** A person's values on one row (a paste); resolves with the outcome
   * so a refusal can be shown in the server's words. Its presence,
   * with `onRemoveRow`, is what arms the right-click menu and the
   * copy and paste keys. */
  onSetCells?: (rowId: string, data: Record<string, string>, how: WriteHow) => Promise<RowOutcome>;
  onRemoveRow?: (rowId: string) => Promise<RowOutcome>;
  /** The sheet's row selection, read and driven here (the gutter's
   * boxes, the header's select all); absent on a grid that cannot
   * select, whose gutter is the row number alone. */
  selection?: RowSelectionControls;
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

  // The cell verbs, one object for the menu and the keys alike. Copy
  // reads the value off the row this grid holds; paste reads the
  // clipboard and hands the value to the sheet's write; delete asks
  // first, in the dialog below. A grid handed no write has no verbs.
  const clipboard = useCellClipboard();
  // The row whose delete is being confirmed, and whether the delete
  // is in flight: ONE state, so a busy question on no row is
  // impossible. The menu that asked is gone by the time the question
  // shows, and a selection of rows will ask the same question with
  // no one row to host it, so it is a dialog and not a strip in a row.
  const [confirming, setConfirming] = useState<{ rowId: string; columnKey: string | null; busy: boolean } | null>(
    null,
  );
  // Where focus lands once a row is gone: the dialog returns it to the
  // menu item that asked, which left with the menu, so a keyboard
  // user would land on nothing. The same column in the next row (the
  // previous one at the end of the sheet) takes it, after the dialog
  // has closed and given focus back.
  const [landing, setLanding] = useState<{ rowId: string; columnKey: string | null } | null>(null);
  useEffect(() => {
    if (!landing || confirming !== null) return;
    const frame = requestAnimationFrame(() => {
      focusCell(bodyRef.current, landing.rowId, landing.columnKey);
      setLanding(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [landing, confirming, bodyRef]);
  // The cell being typed into; the editor holds the draft.
  const [editing, setEditing] = useState<EditingCell | null>(null);
  const cellEditing = useMemo<CellEditing | null>(() => {
    if (!onSetCells) return null;
    return { editing, start: setEditing, done: () => setEditing(null) };
  }, [onSetCells, editing]);
  // The rows a key can move through: the loaded ones.
  const rowIds = useMemo(() => rows.map((row) => row.id), [rows]);
  // The grid's root found by its mark, not a ref: the verbs object
  // that carries this is read at render, and a ref read inside it
  // would mark the whole object as one (the compiler's rule).
  const focusNeighbour = useCallback(
    (rowId: string, columnKey: string, direction: Direction): boolean => {
      const target = neighbour(columns, rowIds, { rowId, columnKey }, direction);
      const root = document.querySelector(`[data-grid-id="${listId}"]`);
      return target !== null && focusCell(root, target.rowId, target.columnKey);
    },
    [columns, rowIds, listId],
  );
  const actions = useMemo<CellActions | null>(() => {
    if (!onSetCells || !onRemoveRow) return null;
    // One route for a value reaching a cell, typed or pasted.
    function writeCell(rowId: string, columnKey: string, value: string, how: WriteHow): Promise<RowOutcome> {
      return onSetCells!(rowId, { [columnKey]: value }, how);
    }
    return {
      copy: (rowId, columnKey) => {
        const column = columns.find((entry) => entry.key === columnKey);
        const row = rows.find((entry) => entry.id === rowId);
        if (!column || !row) return;
        const value = copiedValueFor(column, row.data[columnKey] ?? "");
        if (value !== null) void clipboard.copy(value);
      },
      paste: (rowId, columnKey) => {
        void (async () => {
          const value = await clipboard.read();
          if (value !== null) await writeCell(rowId, columnKey, value, "paste");
        })();
      },
      write: (rowId, columnKey, value) => writeCell(rowId, columnKey, value, "typed"),
      focusNeighbour,
      deleteRow: (rowId, columnKey) => setConfirming({ rowId, columnKey, busy: false }),
    };
  }, [onSetCells, onRemoveRow, columns, rows, clipboard, focusNeighbour]);

  function confirmDelete() {
    if (!confirming || !onRemoveRow) return;
    const { rowId, columnKey } = confirming;
    const index = rows.findIndex((row) => row.id === rowId);
    const neighbour = rows[index + 1] ?? rows[index - 1] ?? null;
    void (async () => {
      setConfirming({ rowId, columnKey, busy: true });
      const outcome = await onRemoveRow(rowId);
      // Gone or refused, the question is answered either way: a
      // refusal is the sheet's toast, and the row stays as it was.
      setConfirming(null);
      if (outcome.ok && neighbour) setLanding({ rowId: neighbour.id, columnKey });
    })();
  }

  const body = (
    <Body
      bodyRef={bodyRef}
      rows={rows}
      items={items}
      totalSize={totalSize}
      scrollMargin={scrollMargin}
      headers={headers}
      searchProvider={searchProvider}
      selection={selection}
    />
  );

  return (
    // closestCenter over a horizontal strip: the pointer sits inside
    // one header cell at a time, so the nearest centre IS the column
    // being displaced.
    <DndContext sensors={sensors} collisionDetection={closestCenter} modifiers={[boundToGrid]} onDragEnd={onDragEnd}>
      {/* overflow-clip (never hidden, which would make this the scroll
          container the sticky bands look for): the resize guide runs
          down the grid and must stop at its bottom edge rather than
          stretch the scroll region's extent under the pointer. */}
      <div ref={gridRef} data-grid-id={listId} className="relative isolate w-max min-w-full overflow-clip text-sm">
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
          selection={selection}
        />
        {tracker && <TrackerRow headers={headers} fills={tracker} />}
        {/* The menu wraps the body whatever it holds: a press on the
            empty sheet's one line finds no row and opens nothing, and
            the body keeps one parent across the zero-rows edge instead
            of being remounted there. */}
        <CellActionsContext.Provider value={actions}>
          <CellEditingContext.Provider value={cellEditing}>
            {actions ? (
              <RowContextMenu columns={columns} actions={actions}>
                {body}
              </RowContextMenu>
            ) : (
              body
            )}
          </CellEditingContext.Provider>
        </CellActionsContext.Provider>
        <ConfirmDialog open={confirming !== null} onClose={() => !confirming?.busy && setConfirming(null)}>
          <ConfirmDelete
            layout="dialog"
            question="Delete row?"
            consequence="Are you sure you want to delete this row?"
            busy={confirming?.busy}
            onCancel={() => setConfirming(null)}
            onDelete={confirmDelete}
          />
        </ConfirmDialog>
      </div>
    </DndContext>
  );
}
