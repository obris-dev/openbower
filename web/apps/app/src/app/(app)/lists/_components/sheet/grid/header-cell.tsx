"use client";

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Popover, PopoverButton, PopoverPanel } from "@bower/ui";
import { useState, type MouseEventHandler } from "react";
import type { ListColumn, WebhookColumn } from "@bower/api";

import type { ColumnOutcome } from "../use-columns";
import { ColumnIcon } from "./column-icon";
import { ColumnMenu } from "./column-menu";
import { ColumnNameField } from "./column-name-field";
import { nudgeColumn } from "../lib/column-order";
import { RESIZE_HANDLE_ATTRIBUTE, ResizeHandle } from "./resize-handle";
import type { CellWidth, GridHeader } from "./use-grid-table";

/** The column menu button's id: what an edit gesture opened from the
 * menu hands focus back to, since the menu item itself is gone by
 * the time the drawer closes. */
export function menuButtonId(key: string): string {
  return `column-menu-${key}`;
}

/** One column's header: the whole cell is the column's MENU button
 * (its icon and its label), and the same cell is the drag handle
 * that reorders it.
 *
 * Both gestures on one element works because they want different
 * things from a press: the menu opens on the CLICK (press and
 * release in place), while the drag sensor arms only once the press
 * has travelled 8px, and a press that travelled is not a click. The
 * click the browser still fires on release after a drag never
 * reaches the button: the sensor swallows it at the document while
 * a drag is live, so a drop does not open the menu. The resize
 * handle sits over the cell's right edge and stops its own press, so
 * a resize is neither a drag nor a click. */
export function HeaderCell({
  header,
  column,
  width,
  columns,
  onReorder,
  onRename,
  onDelete,
  onEditWebhook,
}: {
  header: GridHeader;
  width: CellWidth;
  column: ListColumn;
  columns: ListColumn[];
  onReorder?: (keys: string[]) => void;
  onRename?: (key: string, label: string) => void;
  onDelete?: (column: ListColumn) => Promise<ColumnOutcome>;
  onEditWebhook?: (column: WebhookColumn) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: column.key,
    disabled: !onReorder,
  });

  function nudge(direction: -1 | 1) {
    const keys = nudgeColumn(columns, column.key, direction);
    if (keys) onReorder?.(keys);
  }

  return (
    <div
      ref={setNodeRef}
      // The sensor's listener ONLY, never dnd-kit's `attributes`: those
      // set role="button" and aria-pressed on a cell that is neither,
      // and their keyboard affordance is the sensor this element does
      // not host (the menu's Move left / Move right is the keyboard
      // path). Bound in the CAPTURE phase: the menu button inside
      // prevents the default of every press it receives, and the
      // sensor ignores a press whose default is already prevented, so
      // in the bubbling phase a press on the label could never start
      // a drag. Capture also runs before the resize handle can stop
      // its own press, so the handle's presses are skipped here: a
      // resize is never a drag.
      onMouseDownCapture={(event) => {
        if (event.target instanceof Element && event.target.closest(`[${RESIZE_HANDLE_ATTRIBUTE}]`)) return;
        (listeners?.onMouseDown as MouseEventHandler<HTMLDivElement> | undefined)?.(event);
      }}
      // TRANSLATE, never Transform: dnd-kit scales the dragged node by
      // (hovered column width / its own width), so a Transform would
      // stretch a narrow column's text to a wide one's shape and back
      // on every crossing. Position moves, size does not.
      style={{ width, transform: CSS.Translate.toString(transform), transition }}
      className={`relative h-full flex-none border-b border-r border-hairline ${
        onReorder ? "cursor-grab select-none active:cursor-grabbing" : ""
      } ${
        // Lifted, not hidden: the column keeps its place in the header
        // so the rows beneath do not reflow mid-drag.
        isDragging ? "z-20 opacity-60" : ""
      }`}
    >
      {renaming && onRename ? (
        <div className="flex h-full items-center px-1">
          <ColumnNameField
            label={column.label}
            onDone={(next) => {
              setRenaming(false);
              // Unchanged is not a rename: a PATCH here would spend a
              // request and a re-render to write what is already there.
              if (next && next !== column.label) onRename(column.key, next);
            }}
          />
        </div>
      ) : (
        <Popover className="h-full">
          {({ open }) => (
            <>
              <PopoverButton
                id={menuButtonId(column.key)}
                title={column.label}
                aria-label={`${column.label} column menu`}
                // A right-click opens the same menu a click does: the
                // header has one menu, so the browser's own is not
                // offered here.
                onContextMenu={(event) => {
                  event.preventDefault();
                  if (!open) event.currentTarget.click();
                }}
                className="flex h-full w-full min-w-0 items-center gap-1.5 p-2 text-left text-sm font-medium text-foreground outline-none hover:bg-wash focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-signal"
              >
                <ColumnIcon column={column} />
                <span className="min-w-0 truncate">{column.label}</span>
              </PopoverButton>
              <PopoverPanel anchor="bottom start" focus>
                {({ close }) => (
                  <ColumnMenu
                    column={column}
                    columns={columns}
                    close={close}
                    onMove={(direction) => nudge(direction)}
                    onRename={onRename && (() => setRenaming(true))}
                    onEditWebhook={onEditWebhook && column.kind === "webhook" ? () => onEditWebhook(column) : undefined}
                    onDelete={onDelete && (() => onDelete(column))}
                  />
                )}
              </PopoverPanel>
            </>
          )}
        </Popover>
      )}
      <ResizeHandle header={header} />
    </div>
  );
}
