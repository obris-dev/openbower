"use client";

import { Switch } from "@bower/ui";
import type { ListColumn } from "@bower/api";

/** One switch per column, two to a row, labels only (a label is unique
 * on a sheet, so the key would say nothing the label does not). The
 * grid is a BOUNDED region: past about six rows it scrolls inside
 * itself, so a sheet at the column cap never pushes what follows the
 * picker out of the drawer. The caller chooses which columns are
 * offered (the wait picker offers AI columns only, so the server's
 * column_not_ai refusal is unreachable from here). */
export function ColumnPicker({
  id,
  legend,
  hint,
  columns,
  selected,
  onChange,
  warned,
  describedBy,
}: {
  id: string;
  legend: string;
  hint: string;
  columns: ListColumn[];
  selected: ReadonlySet<string>;
  onChange: (next: Set<string>) => void;
  warned: boolean;
  /** The id of the gap line under the picker, once it shows. */
  describedBy?: string;
}) {
  return (
    <fieldset
      id={id}
      aria-describedby={describedBy}
      className={warned ? "rounded-lg p-3 ring-1 ring-inset ring-warning-edge" : "p-3"}
    >
      <legend className="text-sm font-medium text-foreground">{legend}</legend>
      <p className="mt-1 text-xs text-muted">{hint}</p>
      <div className="mt-3 grid max-h-48 gap-x-6 gap-y-2 overflow-y-auto sm:grid-cols-2">
        {columns.map((column) => (
          <div key={column.key} className="flex items-center justify-between gap-3">
            <p className="min-w-0 truncate text-sm text-foreground">{column.label}</p>
            <Switch
              checked={selected.has(column.key)}
              onChange={(checked) => {
                const next = new Set(selected);
                if (checked) next.add(column.key);
                else next.delete(column.key);
                onChange(next);
              }}
              aria-label={column.label}
            />
          </div>
        ))}
      </div>
    </fieldset>
  );
}
