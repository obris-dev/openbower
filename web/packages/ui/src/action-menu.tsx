"use client";

import * as RadixMenu from "@radix-ui/react-dropdown-menu";
import { ChevronRight } from "lucide-react";
import { createContext, useContext, useState, type ComponentPropsWithoutRef, type ReactNode } from "react";
import { cn } from "./cn";

// A button-opened menu that nests: a group of choices behind one item
// (Add column ›) opens beside the menu rather than lengthening it. Radix,
// because Headless UI's Menu (the Dropdown) has no submenu; the chrome is
// the Dropdown's and the ContextMenu's, so the three read as one family.
// Arrow keys (→ opens a submenu, ← closes it), type-ahead, Escape,
// outside-click and focus return come from the primitive. A choice that
// opens something taking focus (a drawer, a field, a confirm) is
// `onSelectAfterClose`: it runs from the menu's own close-focus hook,
// once focus is back on the menu's button, so what it opens keeps focus
// and reads the button as its return target, whatever the close's
// timing.

// Bounded by the room Radix measures beside the trigger, so a long menu
// scrolls and a long label truncates rather than running off the screen.
const PANEL_CLASSES = cn(
  "z-50 min-w-48 overflow-y-auto overflow-x-hidden rounded-lg border border-hairline bg-surface py-1 shadow-lg outline-none",
  "max-h-(--radix-dropdown-menu-content-available-height) max-w-(--radix-dropdown-menu-content-available-width)",
);
const ITEM_CLASSES = cn(
  "flex w-full cursor-pointer items-center gap-2 px-4 py-2.5 text-left text-sm font-medium text-foreground outline-none",
  "data-[highlighted]:bg-wash data-[disabled]:cursor-default data-[disabled]:opacity-40",
);

/** One menu's hand-off: the button focus returns to, and the choice
 * queued to run once it has. */
type HandOff = {
  holdTrigger: (button: HTMLButtonElement | null) => void;
  queue: (next: () => void) => void;
  /** Focus the button and run the queued choice; false when none was. */
  run: () => boolean;
};

function createHandOff(): HandOff {
  let trigger: HTMLButtonElement | null = null;
  let queued: (() => void) | null = null;
  return {
    holdTrigger(button) {
      trigger = button;
    },
    queue(next) {
      queued = next;
    },
    run() {
      const next = queued;
      if (next === null) return false;
      queued = null;
      trigger?.focus();
      next();
      return true;
    },
  };
}

const HandOffContext = createContext<HandOff | null>(null);

function useHandOff(): HandOff {
  const handOff = useContext(HandOffContext);
  if (handOff === null) throw new Error("ActionMenu parts render inside an ActionMenu.");
  return handOff;
}

export function ActionMenu(props: ComponentPropsWithoutRef<typeof RadixMenu.Root>) {
  const [handOff] = useState(createHandOff);
  return (
    <HandOffContext.Provider value={handOff}>
      <RadixMenu.Root {...props} />
    </HandOffContext.Provider>
  );
}

/** The button that opens the menu: the element handed in IS the button. */
export function ActionMenuTrigger(props: Omit<ComponentPropsWithoutRef<typeof RadixMenu.Trigger>, "asChild">) {
  const { holdTrigger } = useHandOff();
  return <RadixMenu.Trigger ref={holdTrigger} asChild {...props} />;
}

export function ActionMenuContent({
  className,
  align = "end",
  ...props
}: Omit<ComponentPropsWithoutRef<typeof RadixMenu.Content>, "className" | "onCloseAutoFocus"> & {
  className?: string;
}) {
  const handOff = useHandOff();
  function closeFocus(event: Event) {
    // The queued choice takes over the menu's own focus return.
    if (handOff.run()) event.preventDefault();
  }
  return (
    <RadixMenu.Portal>
      <RadixMenu.Content
        align={align}
        sideOffset={4}
        collisionPadding={12}
        {...props}
        onCloseAutoFocus={closeFocus}
        className={cn(PANEL_CLASSES, className)}
      />
    </RadixMenu.Portal>
  );
}

/** One verb; `onSelect` runs it as the menu closes, `onSelectAfterClose`
 * once it has closed and focus is back on its button (for a verb that
 * opens something taking focus). */
export function ActionMenuItem({
  className,
  children,
  onSelect,
  onSelectAfterClose,
  ...props
}: Omit<ComponentPropsWithoutRef<typeof RadixMenu.Item>, "className"> & {
  className?: string;
  onSelectAfterClose?: () => void;
}) {
  const { queue } = useHandOff();
  function select(event: Event) {
    onSelect?.(event);
    if (onSelectAfterClose) queue(onSelectAfterClose);
  }
  return (
    <RadixMenu.Item {...props} onSelect={select} className={cn(ITEM_CLASSES, className)}>
      <span className="min-w-0 truncate">{children}</span>
    </RadixMenu.Item>
  );
}

export function ActionMenuSeparator() {
  return <RadixMenu.Separator className="my-1 h-px bg-hairline" />;
}

export const ActionMenuSub = RadixMenu.Sub;

/** The item that opens a submenu: its label, and the chevron that says it
 * leads somewhere. */
export function ActionMenuSubTrigger({ children }: { children: ReactNode }) {
  return (
    <RadixMenu.SubTrigger className={cn(ITEM_CLASSES, "justify-between data-[state=open]:bg-wash")}>
      <span className="min-w-0 truncate">{children}</span>
      <ChevronRight aria-hidden className="h-4 w-4 shrink-0 text-faint" />
    </RadixMenu.SubTrigger>
  );
}

export function ActionMenuSubContent({ children }: { children: ReactNode }) {
  return (
    <RadixMenu.Portal>
      <RadixMenu.SubContent sideOffset={4} collisionPadding={12} className={PANEL_CLASSES}>
        {children}
      </RadixMenu.SubContent>
    </RadixMenu.Portal>
  );
}
