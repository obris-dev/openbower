"use client";

import { ArrowUpRight, MoveLeft, MoveRight, Pencil, Trash2 } from "lucide-react";
import { PopoverItem } from "@bower/ui";
import { useState } from "react";
import type { ListColumn } from "@bower/api";

import { ConfirmDelete } from "../../../../_components/confirm-delete";
import { DELETE_WEBHOOK_COLUMN_CONSEQUENCE, EDIT_WEBHOOK_VERB } from "../send-webhook";
import type { ColumnOutcome } from "../use-columns";
import { canMove } from "../lib/column-order";

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
export function ColumnMenu({
  column,
  columns,
  close,
  onMove,
  onRename,
  onEditWebhook,
  onDelete,
}: {
  column: ListColumn;
  columns: ListColumn[];
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
      <ConfirmDelete
        question={`Delete the ${column.label} column?`}
        // No cell count: it is a sheet-wide fact the client cannot
        // know from paged rows, and a number we cannot stand behind
        // is worse than none.
        consequence={column.kind === "webhook" ? DELETE_WEBHOOK_COLUMN_CONSEQUENCE : "This deletes the column and everything in it."}
        busy={deleting}
        refusal={refusal}
        onCancel={() => setConfirming(false)}
        onDelete={() => void confirmDelete()}
      />
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
      {column.kind === "webhook" && onEditWebhook && (
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
