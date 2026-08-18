"use client";

import { Popover as HPopover, PopoverButton, PopoverPanel as HPopoverPanel } from "@headlessui/react";
import type { ComponentProps, ComponentPropsWithoutRef } from "react";
import { cn } from "./cn";

/** Anchored disclosure panel for MIXED content (tiered flows, forms):
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
        "[--anchor-gap:0.5rem] transition duration-100 ease-out data-closed:scale-95 data-closed:opacity-0",
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
        "block w-full px-4 py-2.5 text-left text-sm font-medium text-foreground hover:bg-wash focus-visible:bg-wash",
        className,
      )}
    />
  );
}
