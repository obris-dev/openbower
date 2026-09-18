"use client";

import { MouseSensor, useSensor, useSensors } from "@dnd-kit/core";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ArrowUpRight, ChevronDown, MoveLeft, MoveRight, Pencil, Trash2 } from "lucide-react";
import { Input, Popover, PopoverButton, PopoverItem, PopoverPanel } from "@bower/ui";
import { useState } from "react";
import { isNumericColumn, type ListColumn } from "@bower/api";

import { ConfirmDelete } from "../../../_components/confirm-delete";
import type { ColumnKind } from "./lib/column-kind";
import { canMove, nudgeColumn } from "./lib/column-order";
import { DELETE_WEBHOOK_COLUMN_CONSEQUENCE, EDIT_WEBHOOK_VERB } from "./send-webhook";
import type { ColumnOutcome } from "./use-columns";

/** The one sensor a column drag listens to: the MOUSE.
 *
 * No touch sensor: the sheet is a horizontally scrolling grid, so a
 * horizontal drag on its sticky header competes with the primary
 * navigation gesture on a phone, and the usual fix (a long press
 * before the drag arms) makes both worse.
 *
 * No keyboard sensor: dnd-kit's lifts from the element holding these
 * listeners, which is the header CELL, so arming it would mean a
 * focusable th and a second tab stop on every column. The menu
 * reaches the same move from the keyboard (its panel is a Popover, so
 * Tab walks the items rather than the arrow keys a Menu would give),
 * and it is the path touch uses too, so only the mouse gets the
 * shortcut. */
export function useColumnSensors() {
  return useSensors(
    // Distance, not delay: a press on the header must stay ambiguous
    // until it has travelled far enough to be a drag rather than a
    // click on something inside the cell. 8px is past the noise of a
    // click that moved slightly.
    useSensor(MouseSensor, { activationConstraint: { distance: 8 } }),
  );
}

/** The column menu's two tiers: the verbs, and the delete confirm
 * that REPLACES them.
 *
 * A Popover rather than a Dropdown, because a Menu's items are a
 * fixed list and this panel has to swap its whole body. That is the
 * primitive's stated purpose ("MIXED content, tiered flows"), and the
 * roster's row menu is the same shape.
 *
 * Confirming HERE is the point: a confirm that opened somewhere else
 * would make the user re-find which column they were deleting, which
 * is exactly why a far-away confirm has to name the column in its
 * question. */
function ColumnMenu({
  column,
  columns,
  kind,
  close,
  onMove,
  onRename,
  onEditWebhook,
  onDelete,
}: {
  column: ListColumn;
  columns: ListColumn[];
  kind: ColumnKind;
  close: () => void;
  onMove: (direction: -1 | 1) => void;
  onRename?: () => void;
  onEditWebhook?: () => void;
  onDelete?: () => Promise<ColumnOutcome>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // A refusal the server answered stays IN the tier that asked, so the
  // user reads why on the column they were deleting.
  const [refusal, setRefusal] = useState<string | null>(null);

  async function confirmDelete() {
    if (!onDelete) return;
    setDeleting(true);
    setRefusal(null);
    const outcome = await onDelete();
    if (outcome.ok || !outcome.detail) {
      close();
      return;
    }
    setDeleting(false);
    setRefusal(outcome.detail);
  }

  if (confirming && onDelete) {
    return (
      <div className="w-64 px-4 py-1.5">
        <ConfirmDelete
          question={`Delete the ${column.label} column?`}
          // No cell count: it is a sheet-wide fact the client cannot
          // know from paged rows, and a number we cannot stand behind
          // is worse than none.
          consequence={kind === "webhook" ? DELETE_WEBHOOK_COLUMN_CONSEQUENCE : "This deletes the column and everything in it."}
          busy={deleting}
          refusal={refusal}
          onCancel={() => setConfirming(false)}
          onDelete={() => void confirmDelete()}
        />
      </div>
    );
  }
  // Rename leads (it is what the menu is most often opened for), the
  // moves follow, and Delete sits last: the sheet's overflow menu puts
  // danger last for the same reason, so a destructive verb is never
  // where a mis-aimed click lands.
  //
  // A Popover's items do not auto-close on click, so each verb closes
  // the panel itself.
  return (
    <div className="w-52">
      {onRename && (
        <PopoverItem onClick={() => { close(); onRename(); }}>
          <span className="flex items-center gap-2">
            <Pencil aria-hidden className="h-4 w-4 text-faint" />
            Rename
          </span>
        </PopoverItem>
      )}
      {kind === "webhook" && onEditWebhook && (
        <PopoverItem onClick={() => { close(); onEditWebhook(); }}>
          <span className="flex items-center gap-2">
            <ArrowUpRight aria-hidden className="h-4 w-4 text-faint" />
            {EDIT_WEBHOOK_VERB}
          </span>
        </PopoverItem>
      )}
      <PopoverItem disabled={!canMove(columns, column.key, -1)} onClick={() => { close(); onMove(-1); }}>
        <span className="flex items-center gap-2">
          <MoveLeft aria-hidden className="h-4 w-4 text-faint" />
          Move left
        </span>
      </PopoverItem>
      <PopoverItem disabled={!canMove(columns, column.key, 1)} onClick={() => { close(); onMove(1); }}>
        <span className="flex items-center gap-2">
          <MoveRight aria-hidden className="h-4 w-4 text-faint" />
          Move right
        </span>
      </PopoverItem>
      {onDelete && (
        <PopoverItem className="text-danger" onClick={() => setConfirming(true)}>
          <span className="flex items-center gap-2">
            <Trash2 aria-hidden className="h-4 w-4" />
            Delete
          </span>
        </PopoverItem>
      )}
    </div>
  );
}

/** A column's name, being typed. Shared by the header's rename and by
 * the pending cell a new plain column is named in, so naming a column
 * is ONE gesture whether the column exists yet or not.
 *
 * It commits on Enter or blur and abandons on Escape, matching the
 * sheet title's click-to-rename rather than opening a dialog for one
 * field.
 *
 * Its own component so the input MOUNTS when renaming starts, which
 * is what lets autoFocus do the focusing (the house rule: autoFocus
 * on a fresh mount, never a ref-and-querySelector effect). */
export function ColumnNameField({ label, onDone }: { label: string; onDone: (next: string) => void }) {
  const [draft, setDraft] = useState(label);
  return (
    <Input
      autoFocus
      aria-label="Column name"
      // A MIN width, not a width: the cell has nothing else to size it
      // (a new column has no label yet, and a narrow one's label is
      // shorter than the text being typed), so auto table layout would
      // collapse the input to the width of what it currently holds.
      className="w-full min-w-40"
      value={draft}
      // The press must not reach the cell's drag listeners, for the
      // same reason the menu button stops it.
      onMouseDown={(event) => event.stopPropagation()}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => onDone(draft.trim())}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          onDone(draft.trim());
        }
        // Escape abandons: restoring the original THEN closing would
        // race the blur, so the empty string tells the caller nothing
        // changed.
        if (event.key === "Escape") onDone("");
      }}
    />
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
  onRename,
  onDelete,
  onEditWebhook,
}: {
  column: ListColumn;
  columns: ListColumn[];
  onReorder?: (keys: string[]) => void;
  onRename?: (key: string, label: string) => void;
  onDelete?: (column: ListColumn) => Promise<ColumnOutcome>;
  onEditWebhook?: (column: ListColumn) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const kind = column.kind;
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
      {renaming && onRename ? (
        <ColumnNameField
          label={column.label}
          onDone={(next) => {
            setRenaming(false);
            // Unchanged is not a rename: a PATCH here would spend a
            // request and a re-render to write what is already there.
            if (next && next !== column.label) onRename(column.key, next);
          }}
        />
      ) : onReorder === undefined ? (
        label
      ) : (
        <div className={`flex items-center gap-1 ${numeric ? "justify-end" : "justify-between"}`}>
          {label}
          <Popover className="relative">
            <PopoverButton
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
            </PopoverButton>
            <PopoverPanel anchor="bottom start" focus>
              {({ close }) => (
                <ColumnMenu
                  column={column}
                  columns={columns}
                  kind={kind}
                  close={close}
                  onMove={(direction) => nudge(direction)}
                  onRename={onRename && (() => setRenaming(true))}
                  onEditWebhook={onEditWebhook && (() => onEditWebhook(column))}
                  onDelete={onDelete && (() => onDelete(column))}
                />
              )}
            </PopoverPanel>
          </Popover>
        </div>
      )}
    </th>
  );
}
