"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import { Menu, X } from "lucide-react";

export function MobileMenu({ docsUrl, authLinks }: { docsUrl: string; authLinks: ReactNode }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="md:hidden">
      <button
        onClick={() => setOpen(!open)}
        className="cursor-pointer rounded-md p-2 text-ink/60 hover:text-ink dark:text-paper/60 dark:hover:text-paper"
        aria-label="Toggle menu"
        aria-expanded={open}
      >
        {open ? <X aria-hidden className="h-5 w-5" /> : <Menu aria-hidden className="h-5 w-5" />}
      </button>

      {open && (
        <div className="absolute inset-x-0 top-full border-b border-ink/10 bg-paper/95 backdrop-blur-md dark:border-paper/10 dark:bg-ink/95">
          <div className="mx-auto flex max-w-6xl flex-col gap-1 px-6 py-4">
            <a
              href={docsUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="rounded-md px-3 py-2 text-sm font-medium text-ink/60 transition-colors hover:bg-ink/5 hover:text-ink dark:text-paper/60 dark:hover:bg-paper/10 dark:hover:text-paper"
            >
              Docs
            </a>
            <div className="mt-1 flex flex-col gap-1 border-t border-ink/10 pt-2 dark:border-paper/10">
              {authLinks}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
