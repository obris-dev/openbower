"use client";

import { Dialog, DialogBackdrop, DialogPanel, DialogTitle, Transition } from "@headlessui/react";
import { X } from "lucide-react";

import { TouchTarget } from "./button-classes";
import { Fragment, type ReactNode } from "react";
import { cn } from "./cn";

// Panel widths, named so a caller picks a size rather than a class.
// Full-width below its cap: a phone has no room for a gutter, and
// a drawer that keeps one reads as a misplaced modal.
const WIDTHS = {
  md: "max-w-md",
  lg: "max-w-lg",
  xl: "max-w-xl",
  "2xl": "max-w-2xl",
} as const;

export type DrawerWidth = keyof typeof WIDTHS;

/** The house's overlay surface (LAYOUT.md rule 6: drawers and inline
 * editing, never modals): a right-side panel with a pinned header, a
 * scrolling body, and an optional pinned footer whose controls submit
 * body forms through the `form` attribute.
 *
 * Rides Headless UI's Dialog, which owns the containment a hand-rolled
 * dialog gets wrong: the focus trap and the outside-click test count
 * CHILD PORTALS as inside, so a Combobox's option list (anchored to the
 * document root, outside the panel's DOM subtree) stays reachable by
 * Tab and does not read as an outside click; Escape resolves
 * innermost-first, so an open combobox's dismissal is not also the
 * drawer's; and background scroll, inert, and focus restore on close
 * come with it.
 *
 * Presentational: the caller owns `open` and every consequence of a
 * close.
 *
 * The Transition root is this component's own, not Dialog's implicit
 * one, for the two motions Dialog's cannot run: it carries `appear`,
 * so a drawer MOUNTED already open still slides in (Dialog's own root
 * treats that as the steady state and cuts straight to visible), and
 * it exposes `afterLeave`, the only signal a caller can use to keep
 * itself mounted until the panel has finished sliding out. */
export function Drawer({
  open,
  onClose,
  title,
  width = "xl",
  footer,
  afterLeave,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  width?: DrawerWidth;
  /** Pinned under the scrolling body; omitted leaves the body flush. */
  footer?: ReactNode;
  /** Fires once the leave motion has finished. A caller that mounts
   * the drawer only while open (so its state resets per open and no
   * work runs while closed) must defer its unmount to THIS, or the
   * panel is torn out of the DOM mid-slide and reads as vanishing. */
  afterLeave?: () => void;
  children: ReactNode;
}) {
  return (
    <Transition show={open} appear as={Fragment} afterLeave={afterLeave}>
      <Dialog onClose={onClose} className="relative z-50">
        <DialogBackdrop
          transition
          className="fixed inset-0 bg-ink/80 transition duration-300 ease-out data-closed:opacity-0 data-leave:duration-200 motion-reduce:transition-none"
        />
        <DialogPanel
          transition
          className={cn(
            "fixed inset-y-0 right-0 flex w-full flex-col bg-canvas shadow-xl ring-1 ring-hairline",
            "transition duration-300 ease-out data-closed:translate-x-full",
            "data-leave:duration-200 data-leave:ease-in motion-reduce:transition-none",
            WIDTHS[width],
          )}
        >
          <div className="flex h-16 shrink-0 items-center justify-between gap-2 border-b border-hairline px-6">
            <DialogTitle className="min-w-0 truncate text-lg font-semibold text-foreground">{title}</DialogTitle>
            {/* The same a11y floor every other control carries: an
                outline (not ring-offset, whose painted gap haloes on
                dark surfaces) and a 44px touch target, which p-2
                around a 20px icon does not reach on its own. */}
            <button
              type="button"
              onClick={onClose}
              className="relative shrink-0 rounded-md p-2 text-faint transition-colors hover:bg-wash hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-600"
            >
              <TouchTarget>
                <span className="sr-only">Close</span>
                <X aria-hidden className="h-5 w-5" />
              </TouchTarget>
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>

          {footer !== undefined && (
            <div className="shrink-0 space-y-3 border-t border-hairline px-6 py-4">{footer}</div>
          )}
        </DialogPanel>
      </Dialog>
    </Transition>
  );
}
