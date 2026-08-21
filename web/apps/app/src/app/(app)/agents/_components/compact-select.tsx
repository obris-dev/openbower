"use client";

import type { SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@bower/ui";

/** The toolbar-scale native select the builder's list pickers share
 * (the ui Select is Input-scale). Same platform rules as Select:
 * appearance-none removes the native arrow so the anchored chevron
 * can replace it, and caller className sizes the wrapper. */
export function CompactSelect({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className={cn("relative inline-block", className)}>
      <select
        {...props}
        className="w-full appearance-none rounded-md bg-surface py-1 pl-2 pr-6 text-xs text-muted ring-1 ring-inset ring-edge"
      />
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute right-1.5 top-1/2 h-3 w-3 -translate-y-1/2 text-faint"
      />
    </div>
  );
}
