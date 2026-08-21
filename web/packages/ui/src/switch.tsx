"use client";

import { Switch as HSwitch } from "@headlessui/react";
import type { ComponentPropsWithoutRef } from "react";
import { cn } from "./cn";

/** The boolean toggle, on Headless UI's Switch (keyboard + aria come
 * from the primitive; every other prop rides through, like the rest
 * of the primitives). */
export function Switch({
  checked,
  onChange,
  disabled,
  className,
  ...rest
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  className?: string;
} & Omit<ComponentPropsWithoutRef<typeof HSwitch>, "checked" | "onChange" | "disabled" | "className">) {
  return (
    <HSwitch
      checked={checked}
      onChange={onChange}
      disabled={disabled}
      {...rest}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-600",
        // Solid fills that clear 3:1 against the surface-colored knob
        // in BOTH modes: signal-600 (the button's own floor) when on,
        // the control role (solid, never a translucent text role)
        // when off.
        checked ? "bg-signal-600" : "bg-control",
        disabled && "cursor-not-allowed opacity-40",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-3.5 w-3.5 transform rounded-full bg-surface shadow transition",
          checked ? "translate-x-[1.125rem]" : "translate-x-1",
        )}
      />
    </HSwitch>
  );
}
