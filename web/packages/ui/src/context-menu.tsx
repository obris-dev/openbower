"use client";

import * as RadixContextMenu from "@radix-ui/react-context-menu";
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cn } from "./cn";

/** The menu that opens AT THE POINTER (a right click, a long press,
 * Shift+F10 on a focused element) rather than from a button: Radix
 * ContextMenu, the one Radix menu here, because the Headless UI
 * primitives every button-opened menu rides anchor only to their own
 * button. The chrome is the Dropdown's, so the two read as one family;
 * arrow keys, type-ahead, Escape, outside-click and focus return come
 * from the primitive. The trigger is `asChild`: the element handed to
 * it IS the surface, so a whole region can open one menu and read
 * where inside it the press landed. */
export const ContextMenu = RadixContextMenu.Root;

export function ContextMenuTrigger(props: Omit<ComponentPropsWithoutRef<typeof RadixContextMenu.Trigger>, "asChild">) {
  return <RadixContextMenu.Trigger asChild {...props} />;
}

export function ContextMenuContent({
  className,
  ...props
}: Omit<ComponentPropsWithoutRef<typeof RadixContextMenu.Content>, "className"> & { className?: string }) {
  return (
    <RadixContextMenu.Portal>
      <RadixContextMenu.Content
        collisionPadding={12}
        {...props}
        className={cn(
          "z-50 w-48 overflow-hidden rounded-lg border border-hairline bg-surface py-1 shadow-lg outline-none",
          className,
        )}
      />
    </RadixContextMenu.Portal>
  );
}

/** One verb. `shortcut` names the key the same verb answers to on a
 * focused element, shown and never bound here (the surface binds it). */
export function ContextMenuItem({
  className,
  shortcut,
  children,
  ...props
}: Omit<ComponentPropsWithoutRef<typeof RadixContextMenu.Item>, "className" | "children"> & {
  className?: string;
  shortcut?: string;
  children: ReactNode;
}) {
  return (
    <RadixContextMenu.Item
      {...props}
      className={cn(
        "flex w-full cursor-default items-center justify-between gap-3 px-4 py-2.5 text-left text-sm font-medium text-foreground outline-none",
        "data-[highlighted]:bg-wash data-[disabled]:opacity-40",
        className,
      )}
    >
      <span className="min-w-0 truncate">{children}</span>
      {shortcut && (
        <span aria-hidden className="shrink-0 text-xs text-faint">
          {shortcut}
        </span>
      )}
    </RadixContextMenu.Item>
  );
}

export function ContextMenuSeparator() {
  return <RadixContextMenu.Separator className="my-1 h-px bg-hairline" />;
}
