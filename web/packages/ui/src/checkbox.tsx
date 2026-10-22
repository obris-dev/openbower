"use client";

import { Checkbox as HCheckbox } from "@headlessui/react";
import type { ComponentPropsWithoutRef } from "react";
import { cn } from "./cn";

/** The one checkbox, on Headless UI's Checkbox (keyboard, aria and
 * the indeterminate state come from the primitive; every other prop
 * rides through). A Switch is for a setting that applies at once; a
 * Checkbox marks a pick that a later verb acts on (a row to delete).
 * The box is 16px, the mark drawn inline so it needs no icon set. */
export function Checkbox({
  checked,
  indeterminate = false,
  onChange,
  disabled,
  className,
  ...rest
}: {
  checked: boolean;
  indeterminate?: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  className?: string;
} & Omit<ComponentPropsWithoutRef<typeof HCheckbox>, "checked" | "indeterminate" | "onChange" | "disabled" | "className">) {
  const marked = checked || indeterminate;
  return (
    <HCheckbox
      checked={checked}
      indeterminate={indeterminate}
      onChange={onChange}
      disabled={disabled}
      {...rest}
      className={cn(
        "inline-flex size-4 shrink-0 cursor-pointer items-center justify-center rounded border transition",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-600",
        // The same fills as the Switch: signal-600 when marked (the
        // button's own floor), the surface with a hairline when not.
        marked ? "border-signal-600 bg-signal-600" : "border-hairline bg-surface",
        disabled && "cursor-not-allowed opacity-40",
        className,
      )}
    >
      <svg aria-hidden viewBox="0 0 16 16" fill="none" className="size-3 text-white">
        {indeterminate ? (
          <path d="M4 8h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        ) : (
          <path
            d="M3.5 8.5l3 3 6-7"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={cn(!checked && "opacity-0")}
          />
        )}
      </svg>
    </HCheckbox>
  );
}
