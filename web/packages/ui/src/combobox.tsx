"use client";

import {
  Combobox as HCombobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxOption,
  ComboboxOptions,
} from "@headlessui/react";
import { ChevronDown } from "lucide-react";
import { useMemo, useState, type Ref } from "react";
import { cn } from "./cn";

export type ComboboxItem = {
  /** The stored value (opaque to the combobox). */
  value: string;
  /** What the user searches against and sees. */
  label: string;
  /** Optional section header the item renders under. */
  group?: string;
};

/** A searchable single-select over grouped items, on Headless UI's
 * Combobox (keyboard nav, aria, focus handling from the primitive).
 * For long rosters where a native select's scanning breaks down. */
export function Combobox({
  items,
  value,
  onChange,
  warned,
  className,
  ...rest
}: {
  items: ComboboxItem[];
  value: string;
  onChange: (next: string) => void;
  /** Amber ring: incomplete, not wrong (the warning tier). */
  warned?: boolean;
  className?: string;
} & {
  /** Input pass-through (the primitive stays open like the others;
   * Headless UI's generics make a full rest-spread type unsound, so
   * the surface is named). */
  id?: string;
  name?: string;
  placeholder?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  ref?: Ref<HTMLInputElement>;
  "aria-label"?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}) {
  const [query, setQuery] = useState("");
  const grouped = useMemo(() => {
    // The group is part of what the user sees, so it is part of what
    // they can search ("ollama" must find a source named ollama).
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? items.filter(
          (i) => i.label.toLowerCase().includes(needle) || (i.group ?? "").toLowerCase().includes(needle),
        )
      : items;
    // GROUPED, not adjacency-diffed: a filter can interleave groups,
    // and a repeated header reads as a duplicate source.
    const byGroup = new Map<string, ComboboxItem[]>();
    for (const item of filtered) {
      const bucket = byGroup.get(item.group ?? "") ?? [];
      bucket.push(item);
      byGroup.set(item.group ?? "", bucket);
    }
    return [...byGroup.entries()];
  }, [items, query]);
  const selected = items.find((i) => i.value === value) ?? null;

  return (
    <HCombobox
      disabled={rest.disabled}
      value={selected}
      onChange={(item: ComboboxItem | null) => {
        // null is the CLEAR gesture (select-all + delete): emit the
        // empty value instead of swallowing it, or the field can
        // never be emptied.
        onChange(item ? item.value : "");
      }}
      onClose={() => setQuery("")}
    >
      <div className={cn("relative", className)}>
        <ComboboxInput
          {...rest}
          displayValue={(item: ComboboxItem | null) => item?.label ?? ""}
          onChange={(e) => setQuery(e.target.value)}
          className={cn(
            "w-full rounded-md bg-surface px-3 py-2 pr-8 text-sm text-foreground shadow-sm",
            "ring-1 ring-inset placeholder:text-faint",
            "focus:outline-none focus:ring-2 focus:ring-inset",
            "disabled:cursor-not-allowed disabled:opacity-40",
            warned ? "ring-warning-edge focus:ring-warning-edge" : "ring-edge focus:ring-signal",
          )}
        />
        <ComboboxButton aria-label="Show options" className="absolute inset-y-0 right-0 flex items-center pr-2">
          <ChevronDown aria-hidden className="h-4 w-4 text-faint" />
        </ComboboxButton>
        <ComboboxOptions
          transition
          anchor="bottom start"
          className={cn(
            "z-50 max-h-64 w-[var(--input-width)] overflow-y-auto rounded-lg border border-hairline bg-surface py-1 shadow-lg outline-none",
            "[--anchor-gap:0.25rem] transition duration-100 ease-out data-closed:opacity-0",
          )}
        >
          {grouped.length === 0 && <p className="px-3 py-2 text-sm text-muted">No matches.</p>}
          {grouped.map(([group, members]) => (
            // Prefixed keys: a real group literally named "(ungrouped)"
            // must not collide with the ungrouped bucket.
            <div key={group ? `g:${group}` : "u:"}>
              {group && <p className="px-3 pb-1 pt-2 text-xs uppercase tracking-wide text-faint">{group}</p>}
              {members.map((item) => (
                <ComboboxOption
                  key={item.value}
                  value={item}
                  className="cursor-default px-3 py-1.5 text-sm text-foreground data-focus:bg-wash data-selected:font-semibold"
                >
                  {item.label}
                </ComboboxOption>
              ))}
            </div>
          ))}
        </ComboboxOptions>
      </div>
    </HCombobox>
  );
}
