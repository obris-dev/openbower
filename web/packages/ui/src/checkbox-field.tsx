"use client";

import { Field, Label } from "@headlessui/react";

import { Checkbox } from "./checkbox";
import { cn } from "./cn";

/** A Checkbox with its visible label, paired through Headless UI's Field:
 * the label names the box for assistive tech and a click on it toggles
 * the box, so no stand-in click target is ever needed. */
export function CheckboxField({
  label,
  checked,
  onChange,
  disabled,
  className,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <Field disabled={disabled} className={cn("flex items-center gap-2", className)}>
      <Checkbox checked={checked} onChange={onChange} disabled={disabled} />
      <Label className="cursor-pointer select-none whitespace-nowrap text-sm text-foreground data-[disabled]:cursor-not-allowed data-[disabled]:opacity-40">
        {label}
      </Label>
    </Field>
  );
}
