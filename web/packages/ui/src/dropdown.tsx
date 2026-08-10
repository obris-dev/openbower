"use client";

import { Menu, MenuButton, MenuItem, MenuItems, type MenuItemsProps, type MenuProps } from "@headlessui/react";
import { clsx } from "clsx";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

/** The action menu, on Headless UI's Menu (adapted from the paid kit's
 * shape, restyled to bower tokens): keyboard navigation, focus return,
 * outside-click, and viewport-aware anchoring replace the hand-rolled
 * backdrop-button pattern. */
export function Dropdown(props: MenuProps) {
  return <Menu {...props} />;
}

export const DropdownButton = MenuButton;

export function DropdownMenu({
  anchor = "bottom end",
  className,
  ...props
}: { className?: string } & Omit<MenuItemsProps, "as" | "className">) {
  return (
    <MenuItems
      {...props}
      transition
      anchor={anchor}
      className={clsx(
        "z-50 w-48 overflow-hidden rounded-lg border border-ink/10 bg-paper py-1 shadow-lg outline-none",
        "[--anchor-gap:0.5rem] transition duration-100 ease-out data-closed:scale-95 data-closed:opacity-0",
        "dark:border-paper/15 dark:bg-ink",
        className,
      )}
    />
  );
}

const ITEM_CLASSES =
  "block w-full px-4 py-2.5 text-left text-sm font-medium text-ink/80 data-focus:bg-ink/5 data-disabled:opacity-40 dark:text-paper/80 dark:data-focus:bg-paper/10";

export function DropdownItem({
  href,
  className,
  children,
  ...props
}: { href?: string; className?: string; children: ReactNode } & Omit<
  ComponentPropsWithoutRef<"button">,
  "className" | "children"
>) {
  return (
    <MenuItem>
      {href ? (
        <a href={href} className={clsx(ITEM_CLASSES, className)}>
          {children}
        </a>
      ) : (
        <button type="button" className={clsx(ITEM_CLASSES, className)} {...props}>
          {children}
        </button>
      )}
    </MenuItem>
  );
}
