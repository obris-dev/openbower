"use client";

import type { ListColumn, RenderableListRow } from "@bower/api";

import { cellHref, cellLinkIsExternal } from "../../../../_components/cell-link";
import { AiCellState, DegradedToolMark, isDegradedFill, type SearchProviderChoice } from "../fill";
import { WebhookCellState } from "../send-webhook";

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
 * cause, the degraded mark) have a keyboard path to them. */
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
  const value = row.data[column.key] ?? "";
  const entry = column.kind === "ai" ? row.states?.[column.key] : undefined;
  const state = !value ? entry : undefined;
  return (
    <div
      tabIndex={0}
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
