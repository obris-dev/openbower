"use client";

import { FieldError, Input, Label } from "@bower/ui";
import type { ListColumn } from "@bower/api";

const ERROR_ID = "send-webhook-sample-error";

/** The sample row the test sends: one input per payload column,
 * pre-filled from the sheet's first row and editable, so the receiver
 * sees the values a user wants it to see. */
export function SampleEditor({
  id,
  columns,
  values,
  onChange,
  refusal,
}: {
  id: string;
  columns: ListColumn[];
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  refusal: string | null;
}) {
  return (
    <fieldset id={id} className="space-y-3 rounded-lg border border-hairline p-3">
      <legend className="px-1 text-sm font-medium text-foreground">Sample row</legend>
      <p className="text-xs text-muted">The sheet&apos;s first row, as the receiver will get it. Edit any value before sending.</p>
      {columns.map((column) => {
        const inputId = `send-webhook-sample-${column.key}`;
        return (
          <div key={column.key}>
            <Label htmlFor={inputId}>{column.label}</Label>
            <Input
              id={inputId}
              value={values[column.key] ?? ""}
              onChange={(event) => onChange(column.key, event.target.value)}
              className="mt-1"
            />
          </div>
        );
      })}
      {refusal && <FieldError id={ERROR_ID}>{refusal}</FieldError>}
    </fieldset>
  );
}
