"use client";

import { horizontalListSortingStrategy, SortableContext } from "@dnd-kit/sortable";
import type { ColumnType, ListColumn, WebhookColumn } from "@bower/api";

import type { AddColumnKind } from "../add-column";
import type { ColumnOutcome } from "../use-columns";
import { AddColumnCell } from "./add-column-cell";
import { ColumnNameField } from "./column-name-field";
import { GutterCell } from "./gutter-cell";
import { HeaderCell } from "./header-cell";
import { DEFAULT_COLUMN_WIDTH, HEADER_HEIGHT } from "./lib/column-sizing";
import type { GridHeader } from "./use-grid-table";

/** The header band, stuck to the top of the scroll region: the gutter
 * mark, one cell per column (sortable, so a drag displaces the
 * others as it crosses them), the cell a new column is named in, and
 * the trailing Add column. It renders whatever the row count: a
 * column is named here before it exists, so a sheet with no rows yet
 * still has to show the editor, or its first column could never be
 * added. The fixed height is the contract the tracker row's sticky
 * offset relies on. */
export function HeaderRow({
  headers,
  columns,
  onReorder,
  onRenameColumn,
  onDeleteColumn,
  onEditWebhook,
  pendingColumn,
  onNamePending,
  onAddColumn,
}: {
  headers: GridHeader[];
  columns: ListColumn[];
  onReorder?: (keys: string[]) => void;
  onRenameColumn?: (key: string, label: string) => void;
  onDeleteColumn?: (column: ListColumn) => Promise<ColumnOutcome>;
  onEditWebhook?: (column: WebhookColumn) => void;
  pendingColumn?: { type: ColumnType } | null;
  onNamePending?: (label: string) => void;
  onAddColumn?: (kind: AddColumnKind) => void;
}) {
  return (
    <div
      className="sticky top-0 z-20 flex w-max min-w-full border-y border-hairline bg-surface"
      style={{ height: HEADER_HEIGHT }}
    >
      <GutterCell className="z-30">#</GutterCell>
      <SortableContext items={columns.map((column) => column.key)} strategy={horizontalListSortingStrategy}>
        {headers.map((header) => (
          <HeaderCell
            key={header.id}
            header={header}
            column={header.column.columnDef.meta!.column}
            columns={columns}
            onReorder={onReorder}
            onRename={onRenameColumn}
            onDelete={onDeleteColumn}
            onEditWebhook={onEditWebhook}
          />
        ))}
      </SortableContext>
      {pendingColumn && onNamePending && (
        // The new column is named BEFORE it exists, because its
        // key derives from the label server-side and a rename
        // never moves a key: creating first would leave a column
        // called "Revenue" keyed on whatever placeholder it was
        // born with.
        <div className="flex flex-none items-center border-r border-hairline px-1" style={{ width: DEFAULT_COLUMN_WIDTH }}>
          <ColumnNameField label="" onDone={onNamePending} />
        </div>
      )}
      {onAddColumn && <AddColumnCell onAddColumn={onAddColumn} />}
    </div>
  );
}
