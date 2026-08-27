"use client";

import { Popover as HPopover, PopoverButton, PopoverPanel as HPopoverPanel } from "@headlessui/react";
import type { ComponentProps, ComponentPropsWithoutRef } from "react";
import { cn } from "./cn";

/** Anchored disclosure panel for MIXED content (tiered flows, forms):
 *
 * --anchor-padding is the VIEWPORT margin the flip and shift
 * middleware keep. It defaults to zero, which lets a panel sit flush
 * against a screen edge, and it is set HERE rather than at call sites
 * because every caller passes `anchor` as a plain string: an object
 * would be needed to carry padding, so one forgotten call site is a
 * panel hugging the edge on a small screen.
 *
 * unlike Dropdown's Menu, items are ordinary focusables, so Tab walks
 * them and clicks do not auto-close. Escape and outside-click still
 * dismiss, and focus returns to the trigger. */
export const Popover = HPopover;
export { PopoverButton };

export function PopoverPanel({
  className,
  ...props
}: Omit<ComponentProps<typeof HPopoverPanel>, "className"> & { className?: string }) {
  return (
    <HPopoverPanel
      transition
      anchor="bottom end"
      {...props}
      className={cn(
        "z-50 overflow-hidden rounded-lg border border-hairline bg-surface py-1 shadow-lg outline-none",
        "[--anchor-gap:0.5rem] [--anchor-padding:0.75rem] transition duration-100 ease-out data-closed:scale-95 data-closed:opacity-0",
        className,
      )}
    />
  );
}

/** The panel's standard row, matching DropdownItem's chrome so the two
 * menu primitives read as one family. */
export function PopoverItem({ className, ...props }: ComponentPropsWithoutRef<"button">) {
  return (
    <button
      type="button"
      {...props}
      className={cn(
        // disabled:, where DropdownItem uses data-disabled: (Headless
        // UI stamps that attribute; this is a plain button).
        "block w-full px-4 py-2.5 text-left text-sm font-medium text-foreground hover:bg-wash focus-visible:bg-wash disabled:opacity-40",
        className,
      )}
    />
  );
}
