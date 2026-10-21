"use client";

import type { ListColumn, RenderableListRow } from "@bower/api";

import type { KeyboardEvent } from "react";

import { cellHref, cellLinkIsExternal } from "../../../../_components/cell-link";
import { AiCellState, DegradedToolMark, isDegradedFill, type SearchProviderChoice } from "../fill";
import { WebhookCellState } from "../send-webhook";
import { useCellActions } from "./cell-actions";
import { acceptsPaste } from "./lib/cell-clipboard";

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
 * offers, bound here when the grid can write. The marks on the
 * element are how that menu learns which cell was pressed. */
export function GridCell({
  column,
  row,
  width,
  searchProvider,
}: {
  column: ListColumn;
  row: RenderableListRow;
  width: number;
  searchProvider: SearchProviderChoice;
}) {
  const actions = useCellActions();
  const value = row.data[column.key] ?? "";
  const entry = column.kind === "ai" ? row.states?.[column.key] : undefined;
  const state = !value ? entry : undefined;

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!actions || !acceptsPaste(column) || !(event.metaKey || event.ctrlKey)) return;
    // The plain chord only: Shift or Alt makes another shortcut (or
    // the browser's), and the letter is read case-blind so Caps Lock
    // does not defeat it.
    if (event.shiftKey || event.altKey) return;
    // Only the cell itself: a key pressed inside a popover's textarea
    // or on a link within the cell is that element's to handle.
    if (event.target !== event.currentTarget) return;
    const key = event.key.toLowerCase();
    if (key === "c") {
      event.preventDefault();
      actions.copy(row.id, column.key);
    } else if (key === "v") {
      event.preventDefault();
      actions.paste(row.id, column.key);
    }
  }

  return (
    <div
      tabIndex={0}
      data-row-id={row.id}
      data-column-key={column.key}
      onKeyDown={onKeyDown}
      className="flex h-full min-w-0 flex-none items-center gap-2 border-b border-r border-hairline p-2 text-sm tabular-nums outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-signal"
      style={{ width }}
    >
      {column.kind === "webhook" ? (
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
