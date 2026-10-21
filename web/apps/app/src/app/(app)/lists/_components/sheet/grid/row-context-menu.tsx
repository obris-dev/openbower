"use client";

import { useState, type ReactNode } from "react";
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuTrigger } from "@bower/ui";
import type { ListColumn } from "@bower/api";

import { acceptsPaste } from "./lib/cell-clipboard";
import { shortcutLabel } from "./lib/shortcut-label";
import type { CellActions } from "./cell-actions";

/** Where a press landed: the row, and the column when it landed on a
 * cell (the gutter and the trailing cell are a row's and no column's). */
type Target = { rowId: string; columnKey: string | null };

// Where the keyboard's menu opens inside the focused cell: in from
// its bottom-left corner, so it reads as the cell's and never sits on
// the hairline. Binary.
const MENU_KEY_INSET = 6;

/** The right-click menu over the body: ONE menu for every row, which
 * reads the cell the press landed on off the element's marks and
 * offers that cell's verbs. Copy and Paste need a column that holds a
 * value; Delete row needs only the row. The keyboard reaches it too:
 * Shift+F10 and the Menu key on a focused cell open it at that cell.
 * Not every browser turns those keys into the context-menu event
 * itself (none does on a Mac), so the trigger fires that event at the
 * cell's own corner and the one pointer handler takes it from there.
 * A touch long-press opens the menu from the primitive's own timer
 * with no context-menu event, so the press itself is read too, and
 * the target is cleared when the menu closes: a verb never acts on
 * the row of an earlier press. */
export function RowContextMenu({
  columns,
  actions,
  children,
}: {
  columns: ListColumn[];
  actions: CellActions;
  children: ReactNode;
}) {
  const [target, setTarget] = useState<Target | null>(null);
  const column = target?.columnKey ? columns.find((entry) => entry.key === target.columnKey) : undefined;
  const copyable = column !== undefined && acceptsPaste(column);
  const pasteable = copyable;

  function aim(pressed: EventTarget | null): boolean {
    const cell = (pressed as Element | null)?.closest<HTMLElement>("[data-row-id]");
    if (!cell?.dataset.rowId) return false;
    setTarget({ rowId: cell.dataset.rowId, columnKey: cell.dataset.columnKey ?? null });
    return true;
  }

  return (
    <ContextMenu onOpenChange={(open) => !open && setTarget(null)}>
      <ContextMenuTrigger
        onPointerDown={(event) => {
          if (event.pointerType !== "mouse") aim(event.target);
        }}
        onContextMenu={(event) => {
          if (aim(event.target)) return;
          // Not a row (a popover's panel over the body): no menu. Ours
          // has no row to act on, and the browser's rides the same
          // default this one does, so neither opens.
          event.preventDefault();
        }}
        onKeyDown={(event) => {
          if (!((event.shiftKey && event.key === "F10") || event.key === "ContextMenu")) return;
          const cell = (event.target as Element).closest<HTMLElement>("[data-row-id]");
          if (!cell) return;
          event.preventDefault();
          const rect = cell.getBoundingClientRect();
          cell.dispatchEvent(
            new MouseEvent("contextmenu", {
              bubbles: true,
              cancelable: true,
              clientX: rect.left + MENU_KEY_INSET,
              clientY: rect.bottom - MENU_KEY_INSET,
            }),
          );
        }}
      >
        <div>{children}</div>
      </ContextMenuTrigger>
      {/* The content renders only while open, in the browser, so the
          platform is read at render. */}
      <ContextMenuContent>
        <ContextMenuItem
          disabled={!copyable}
          shortcut={shortcutLabel("C", navigator.platform)}
          onSelect={() => target?.columnKey && actions.copy(target.rowId, target.columnKey)}
        >
          Copy
        </ContextMenuItem>
        <ContextMenuItem
          disabled={!pasteable}
          shortcut={shortcutLabel("V", navigator.platform)}
          onSelect={() => target?.columnKey && actions.paste(target.rowId, target.columnKey)}
        >
          Paste
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          className="text-danger"
          disabled={target === null}
          onSelect={() => target && actions.deleteRow(target.rowId, target.columnKey)}
        >
          Delete row
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}
