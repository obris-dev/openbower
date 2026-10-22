"use client";

import { Dialog, DialogBackdrop, DialogPanel, DialogTitle } from "@headlessui/react";
import type { ReactNode } from "react";

/** The one modal: the confirm for the two destructive acts LAYOUT.md
 * rule 6 names (a row's delete from a menu that is gone when the
 * question is asked, and the selection pill's delete). It
 * holds the shared confirm form and nothing else; Escape, the
 * backdrop, the focus trap and the focus return come from Headless
 * UI's Dialog, and focus lands where the form marks it
 * (data-autofocus on Cancel). The panel sits in the upper fifth of
 * the viewport rather than its centre, where the eye already is on a
 * sheet, and holds one width whatever the question's length. Every
 * other confirm stays in place. */
export function ConfirmDialog({
  open,
  onClose,
  children,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Dialog open={open} onClose={onClose} className="relative z-50">
      <DialogBackdrop
        transition
        className="fixed inset-0 bg-ink/40 transition duration-100 ease-out data-closed:opacity-0 motion-reduce:transition-none"
      />
      <div className="fixed inset-0 flex items-start justify-center px-4 pt-[20vh]">
        <DialogPanel
          transition
          className="w-full max-w-[32rem] rounded-2xl bg-surface p-6 shadow-lg ring-1 ring-hairline transition duration-100 ease-out data-closed:scale-95 data-closed:opacity-0 motion-reduce:transition-none"
        >
          {children}
        </DialogPanel>
      </div>
    </Dialog>
  );
}

/** The dialog's question as its heading: Headless UI names the dialog
 * by it (aria-labelledby), so assistive tech reads the question when
 * focus lands inside. The form renders it; the dialog owns its
 * look. */
export function ConfirmDialogTitle({ children }: { children: ReactNode }) {
  return (
    <DialogTitle as="h2" className="text-sm font-semibold leading-6 text-foreground">
      {children}
    </DialogTitle>
  );
}
