"use client";

import type { ListColumn, RenderableListRow } from "@bower/api";

import type { KeyboardEvent, MouseEvent, PointerEvent } from "react";

import { cellHref, cellLinkIsExternal } from "../../../../_components/cell-link";
import { AiCellState, DegradedToolMark, isDegradedFill, type SearchProviderChoice } from "../fill";
import { WebhookCellState } from "../send-webhook";
import { useCellActions } from "./cell-actions";
import { useCellEditing } from "./cell-editing";
import { CellEditor } from "./cell-editor";
import { acceptsPaste } from "./lib/cell-clipboard";
import { isEditable, type Direction } from "./lib/cell-navigation";
import type { CellWidth } from "./use-grid-table";

const ARROWS: Record<string, Direction> = {
  ArrowUp: "up",
  ArrowDown: "down",
  ArrowLeft: "left",
  ArrowRight: "right",
};

function Value({ column, value }: { column: ListColumn; value: string }) {
  const href = cellHref(column.type, value);
  if (href) {
    return (
      <a
        href={href}
        {...(cellLinkIsExternal(column.type) ? { target: "_blank", rel: "noreferrer" } : {})}
        className="truncate text-muted hover:text-signal"
      >
        {value}
      </a>
    );
  }
  return <span className="truncate text-foreground">{value}</span>;
}

/** One cell of one row: a fixed box the column's width, its content
 * on one line and clipped at the edge. What it holds depends on the
 * column's kind. A webhook column holds no value, so its cell is its
 * state off the row's ledger. An AI cell renders its state only while
 * it has no value: a REAL VALUE always outranks a state, with a mark
 * beside it when the row's run had a degraded tool. Both come off
 * THIS row object, so a value and its state can never come from
 * different requests and disagree. A plain cell is the value, or
 * nothing. The cell is focusable so the popovers inside (a state's
 * cause, the degraded mark) have a keyboard path to them, and so the
 * copy and paste keys land on it: the same verbs the right-click menu
 * offers, bound here when the grid can write. The ring shows on any
 * focus, a click's included (the sheet's cell cursor), not only the
 * keyboard's. The marks on the
 * element are how that menu learns which cell was pressed. On a grid
 * that can write, a cell is also where typing lands: a printable key
 * opens the cell's editor with that character (typing replaces),
 * Enter, F2 or a double-click opens it on the value, the arrows move
 * between cells and Tab between editable ones. */
export function GridCell({
  column,
  row,
  width,
  searchProvider,
}: {
  column: ListColumn;
  row: RenderableListRow;
  width: CellWidth;
  searchProvider: SearchProviderChoice;
}) {
  const actions = useCellActions();
  const editing = useCellEditing();
  const value = row.data[column.key] ?? "";
  const entry = column.kind === "ai" ? row.states?.[column.key] : undefined;
  const state = !value ? entry : undefined;
  const isEditing = editing?.editing?.rowId === row.id && editing.editing.columnKey === column.key;
  const editable = editing !== null && isEditable(column);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // Only the cell itself: a key pressed inside a popover's textarea
    // or on a link within the cell is that element's to handle.
    if (!actions || event.target !== event.currentTarget) return;
    if (event.metaKey || event.ctrlKey) {
      // The plain chord only: Shift or Alt makes another shortcut (or
      // the browser's), and the letter is read case-blind so Caps
      // Lock does not defeat it.
      if (event.shiftKey || event.altKey || !acceptsPaste(column)) return;
      const key = event.key.toLowerCase();
      if (key === "c") {
        event.preventDefault();
        actions.copy(row.id, column.key);
      } else if (key === "v") {
        event.preventDefault();
        actions.paste(row.id, column.key);
      }
      return;
    }
    const direction = ARROWS[event.key];
    if (direction) {
      // Every column, a webhook's cell included; at an edge the browser
      // keeps the key (the scroll region moves). Alt and Shift chords
      // are the browser's (back, forward, a text selection).
      if (event.altKey || event.shiftKey) return;
      if (actions.focusNeighbour(row.id, column.key, direction)) event.preventDefault();
      return;
    }
    if (event.key === "Tab" && editable) {
      // Over the editable cells; falls through to the page's order at
      // the row's edge.
      const moved = actions.focusNeighbour(row.id, column.key, event.shiftKey ? "left" : "right", "editable");
      if (moved) event.preventDefault();
      return;
    }
    if (!editable || event.altKey) return;
    if (event.key === "Enter" || event.key === "F2") {
      event.preventDefault();
      editing.start({ rowId: row.id, columnKey: column.key, initial: value });
    } else if (event.key.length === 1) {
      event.preventDefault();
      editing.start({ rowId: row.id, columnKey: column.key, initial: event.key });
    }
  }

  // A right-click focuses the cell too, so the one the menu opened on
  // wears the ring; a left click focuses it by default.
  // A press on a link or a mark inside the cell is theirs; a press on
  // the value's own text is the cell's, as a press on its padding is.
  function pressOwnedInside(target: EventTarget | null): boolean {
    return (target as Element | null)?.closest("a, button, [role=button]") !== null;
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button === 2 && !pressOwnedInside(event.target)) event.currentTarget.focus({ preventScroll: true });
  }

  function onDoubleClick(event: MouseEvent<HTMLDivElement>) {
    if (!editable || isEditing || pressOwnedInside(event.target)) return;
    editing.start({ rowId: row.id, columnKey: column.key, initial: value });
  }

  return (
    <div
      tabIndex={0}
      data-row-id={row.id}
      data-column-key={column.key}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onDoubleClick={onDoubleClick}
      className="relative flex h-full min-w-0 flex-none items-center gap-2 border-b border-r border-hairline p-2 text-sm tabular-nums outline-none focus:ring-2 focus:ring-inset focus:ring-signal"
      style={{ width }}
    >
      {isEditing ? (
        <CellEditor rowId={row.id} columnKey={column.key} initial={editing.editing!.initial} current={value} />
      ) : column.kind === "webhook" ? (
        <WebhookCellState entry={row.states?.[column.key]} />
      ) : state !== undefined ? (
        <AiCellState entry={state} searchProvider={searchProvider} />
      ) : (
        <>
          {value && <Value column={column} value={value} />}
          {isDegradedFill(entry) && <DegradedToolMark tools={entry.tools} searchProvider={searchProvider} />}
        </>
      )}
    </div>
  );
}
