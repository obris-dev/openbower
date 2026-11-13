"use client";

import { horizontalListSortingStrategy, SortableContext } from "@dnd-kit/sortable";
import { Checkbox } from "@bower/ui";
import type { ColumnType, SheetColumn, WebhookColumn } from "@bower/api";

import type { AddColumnKind } from "../add-column";
import type { ColumnOutcome } from "../use-columns";
import { AddColumnCell } from "./add-column-cell";
import { ColumnNameField } from "./column-name-field";
import { GutterCell } from "./gutter-cell";
import { LoggedHeader } from "./logged-cell";
import { HeaderCell } from "./header-cell";
import { DEFAULT_COLUMN_WIDTH, HEADER_HEIGHT } from "./lib/column-sizing";
import type { RowSelectionControls } from "./row-selection-controls";
import type { CellWidth, GridHeader } from "./use-grid-table";

/** The header band, stuck to the top of the scroll region: the gutter
 * mark (the select-all box on a sheet that can be selected: every
 * row of the sheet, loaded or not, or on a log the rows loaded from its
 * period; a mark for a part), a log's Logged, one cell per column (sortable, so a drag displaces the
 * others as it crosses them), the cell a new column is named in, and
 * the trailing Add column. It renders whatever the row count: a
 * column is named here before it exists, so a sheet with no rows yet
 * still has to show the editor, or its first column could never be
 * added. The fixed height is the contract the tracker row's sticky
 * offset relies on. */
export function HeaderRow({
  headers,
  widthOf,
  columns,
  onReorder,
  onRenameColumn,
  onDeleteColumn,
  onEditWebhook,
  pendingColumn,
  onNamePending,
  onAddColumn,
  logged = false,
  selection,
}: {
  headers: GridHeader[];
  widthOf: (header: GridHeader) => CellWidth;
  columns: SheetColumn[];
  selection?: RowSelectionControls;
  onReorder?: (keys: string[]) => void;
  onRenameColumn?: (key: string, label: string) => void;
  onDeleteColumn?: (column: SheetColumn) => Promise<ColumnOutcome>;
  onEditWebhook?: (column: WebhookColumn) => void;
  pendingColumn?: { type: ColumnType } | null;
  onNamePending?: (label: string) => void;
  onAddColumn?: (kind: AddColumnKind) => void;
  /** A log's header: Logged leads the columns. */
  logged?: boolean;
}) {
  return (
    <div
      data-sticky-top=""
      className="sticky top-0 z-20 flex w-max min-w-full bg-surface"
      style={{ height: HEADER_HEIGHT }}
    >
      <GutterCell className="z-30 border-b">
        {selection ? (
          <Checkbox
            data-select-all=""
            aria-label="Select all rows"
            checked={selection.headerState === "all"}
            indeterminate={selection.headerState === "some"}
            disabled={selection.disabled}
            onChange={selection.onToggleAll}
          />
        ) : (
          "#"
        )}
      </GutterCell>
      {logged && <LoggedHeader />}
      <SortableContext items={columns.map((column) => column.key)} strategy={horizontalListSortingStrategy}>
        {headers.map((header) => (
          <HeaderCell
            key={header.id}
            header={header}
            width={widthOf(header)}
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
        <div className="flex flex-none items-center border-b border-r border-hairline px-1" style={{ width: DEFAULT_COLUMN_WIDTH }}>
          <ColumnNameField label="" onDone={onNamePending} />
        </div>
      )}
      {onAddColumn && <AddColumnCell onAddColumn={onAddColumn} />}
    </div>
  );
}
