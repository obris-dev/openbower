"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { MoreHorizontal, Pencil } from "lucide-react";
import { Popover, PopoverButton, PopoverItem, PopoverPanel } from "@bower/ui";

import { EDIT_ACTION, EDIT_SIGNAL_ACTION, VIEW_ACTION } from "./copy";

const ICON_BUTTON = "rounded p-1 text-faint hover:bg-wash hover:text-foreground";

/** A signal row's verbs: the pencil is the one-click way to its settings,
 * the menu holds every verb (the row's name already opens its sheet, so
 * the menu repeats it for a keyboard user who tabbed past). Delete only
 * asks: the list opens the confirm dialog, which holds still while the
 * signals service answers, where this menu would close on an outside
 * click mid-request. */
export function SignalRowActions({
  id,
  label,
  editHref,
  viewHref,
  onDelete,
}: {
  id: string;
  label: string;
  editHref: string;
  viewHref: string;
  onDelete: () => void;
}) {
  return (
    <div className="flex items-center justify-end gap-1">
      <Link href={editHref} aria-label={`${EDIT_SIGNAL_ACTION}: ${label}`} title={EDIT_ACTION} className={ICON_BUTTON}>
        <Pencil aria-hidden className="h-4 w-4" />
      </Link>
      <Popover className="relative inline-block">
        <PopoverButton aria-label={`Signal actions: ${label}`} data-signal-actions={id} className={ICON_BUTTON}>
          <MoreHorizontal aria-hidden className="h-4 w-4" />
        </PopoverButton>
        <PopoverPanel focus>
          {({ close }) => <MenuBody editHref={editHref} viewHref={viewHref} onDelete={onDelete} close={close} />}
        </PopoverPanel>
      </Popover>
    </div>
  );
}

function MenuBody({
  editHref,
  viewHref,
  onDelete,
  close,
}: {
  editHref: string;
  viewHref: string;
  onDelete: () => void;
  close: () => void;
}) {
  const router = useRouter();
  return (
    <div className="w-48">
      <PopoverItem
        onClick={() => {
          close();
          router.push(viewHref);
        }}
      >
        {VIEW_ACTION}
      </PopoverItem>
      <PopoverItem
        onClick={() => {
          close();
          router.push(editHref);
        }}
      >
        {EDIT_ACTION}
      </PopoverItem>
      <div className="mx-4 my-1 border-t border-hairline" />
      <PopoverItem
        className="text-danger"
        onClick={() => {
          close();
          onDelete();
        }}
      >
        Delete
      </PopoverItem>
    </div>
  );
}
