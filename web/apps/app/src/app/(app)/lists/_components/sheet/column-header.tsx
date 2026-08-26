"use client";

import { MouseSensor, useSensor, useSensors } from "@dnd-kit/core";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ChevronDown, MoveLeft, MoveRight } from "lucide-react";
import { Dropdown, DropdownButton, DropdownItem, DropdownMenu } from "@bower/ui";
import { isNumericColumn, type ListColumn } from "@bower/api";

import { canMove, nudgeColumn } from "./lib/column-order";

/** The one sensor a column drag listens to: the MOUSE.
 *
 * No touch sensor: the sheet is a horizontally scrolling grid, so a
 * horizontal drag on its sticky header competes with the primary
 * navigation gesture on a phone, and the usual fix (a long press
 * before the drag arms) makes both worse.
 *
 * No keyboard sensor: dnd-kit's lifts from the element holding these
 * listeners, which is the header CELL, so arming it would mean a
 * focusable th and a second tab stop on every column. The menu is
 * already the keyboard path to the same move, and the one touch uses,
 * so only the mouse gets the shortcut. */
export function useColumnSensors() {
  return useSensors(
    // Distance, not delay: a press on the header must stay ambiguous
    // until it has travelled far enough to be a drag rather than a
    // click on something inside the cell. 8px is past the noise of a
    // click that moved slightly.
    useSensor(MouseSensor, { activationConstraint: { distance: 8 } }),
  );
}

/** One column's header: the label, which is also the drag handle, and
 * a chevron opening the column's own MENU (move now, rename and
 * delete later).
 *
 * The drag and the menu MUST live on different elements: the two
 * gestures want opposite things from the same press, since a menu
 * opens on contact while a drag has to stay undecided for 8px. So the
 * cell drags, the chevron inside it opens the menu, and the chevron
 * stops the press from reaching the cell (see its handler below). */
export function ColumnHeader({
  column,
  columns,
  onReorder,
}: {
  column: ListColumn;
  columns: ListColumn[];
  onReorder?: (keys: string[]) => void;
}) {
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: column.key,
    disabled: !onReorder,
  });

  function nudge(direction: -1 | 1) {
    const keys = nudgeColumn(columns, column.key, direction);
    if (keys) onReorder?.(keys);
  }

  const numeric = isNumericColumn(column);
  const label = (
    // Truncation needs a BLOCK: max-w on a table cell is inert under
    // auto table layout.
    <span className="block max-w-64 truncate">{column.label}</span>
  );

  return (
    <th
      ref={setNodeRef}
      // Listeners ONLY, never dnd-kit's `attributes`: those set
      // role="button" and aria-pressed on a th that is neither, and
      // their keyboard affordance is the sensor this element does not
      // host.
      {...listeners}
      // TRANSLATE, never Transform: dnd-kit scales the dragged node by
      // (hovered column width / its own width), so a Transform would
      // stretch a narrow column's text to a wide one's shape and back
      // on every crossing. Position moves, size does not.
      style={{ transform: CSS.Translate.toString(transform), transition }}
      title={column.label}
      className={`sticky top-0 bg-surface px-4 py-3 font-medium ${numeric ? "text-right" : ""} ${
        onReorder ? "cursor-grab select-none hover:text-foreground active:cursor-grabbing" : ""
      } ${
        // Lifted, not hidden: the column keeps its place in the header
        // so the rows beneath do not reflow mid-drag.
        isDragging ? "z-20 opacity-60" : ""
      }`}
    >
      {onReorder === undefined ? (
        label
      ) : (
        <div className={`flex items-center gap-1 ${numeric ? "justify-end" : "justify-between"}`}>
          {label}
          <Dropdown>
            <DropdownButton
              // The menu button sits INSIDE the drag surface, so its
              // press must not reach the cell's drag listeners. Stated
              // here rather than left to Headless UI cancelling its
              // own pointerdown: that works today, but it is a
              // dependency's internals and invisible from this file.
              onMouseDown={(event) => event.stopPropagation()}
              aria-label={`${column.label} column menu`}
              // The hit area grows through an ::after overlay, never
              // through padding: the header row's h-11 is what makes
              // the tracker row's top-11 exact, and py-3 leaves this
              // button 20px of content height to live in. 28px clears
              // the pointer minimum; touch gets the full 44px, where
              // the overlay costs nothing because no touch gesture
              // drags a column anyway.
              className="relative shrink-0 rounded p-0.5 opacity-0 transition-opacity after:absolute after:-inset-1.5 after:content-[''] hover:bg-wash hover:text-foreground focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-600 group-hover/header:opacity-100 [@media(hover:none)]:opacity-100 [@media(hover:none)]:after:-inset-3.5"
            >
              <ChevronDown aria-hidden className="h-3 w-3" />
            </DropdownButton>
            <DropdownMenu anchor="bottom start">
              <DropdownItem onClick={() => nudge(-1)} disabled={!canMove(columns, column.key, -1)}>
                {/* The flex span is load-bearing: the item is a BLOCK
                    and preflight makes an svg one too, so an icon
                    passed as a bare sibling of the text takes its own
                    line. Same wrapper the add-column menu uses. */}
                <span className="flex items-center gap-2">
                  <MoveLeft aria-hidden className="h-4 w-4 text-faint" />
                  Move left
                </span>
              </DropdownItem>
              <DropdownItem onClick={() => nudge(1)} disabled={!canMove(columns, column.key, 1)}>
                <span className="flex items-center gap-2">
                  <MoveRight aria-hidden className="h-4 w-4 text-faint" />
                  Move right
                </span>
              </DropdownItem>
            </DropdownMenu>
          </Dropdown>
        </div>
      )}
    </th>
  );
}
