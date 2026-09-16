"use client";

import { Switch } from "@bower/ui";
import type { ListColumn } from "@bower/api";

/** One switch per column, the tool-toggles layout: the label over its
 * key. The caller chooses which columns are offered (the wait picker
 * offers AI columns only, so the server's column_not_ai refusal is
 * unreachable from here; the payload picker offers every column). */
export function ColumnPicker({
  id,
  legend,
  hint,
  columns,
  selected,
  onChange,
  warned,
}: {
  id: string;
  legend: string;
  hint: string;
  columns: ListColumn[];
  selected: ReadonlySet<string>;
  onChange: (next: Set<string>) => void;
  warned: boolean;
}) {
  return (
    <fieldset
      id={id}
      className={
        warned
          ? "space-y-3 rounded-lg p-3 ring-1 ring-inset ring-warning-edge"
          : "space-y-3 rounded-lg border border-hairline p-3"
      }
    >
      <legend className="px-1 text-sm font-medium text-foreground">{legend}</legend>
      <p className="text-xs text-muted">{hint}</p>
      {columns.map((column) => (
        <div key={column.key} className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-sm text-foreground [overflow-wrap:anywhere]">{column.label}</p>
            <p className="font-mono text-xs text-faint">{column.key}</p>
          </div>
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
    </fieldset>
  );
}
