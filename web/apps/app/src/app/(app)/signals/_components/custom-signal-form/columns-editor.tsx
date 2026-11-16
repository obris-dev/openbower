"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { Button, FieldError, Input, Select } from "@bower/ui";
import { COLUMN_LABEL_MAX_LENGTH, COLUMN_TYPES, MAX_SIGNAL_COLUMNS, type ColumnType } from "@bower/api";

import { columnTypeLabel } from "../../../_components/column-type-meta";
import { ADD_COLUMN, COLUMN_PLACEHOLDER, FIELD_PREFIX, columnsCapNote } from "./copy";
import { EMPTY_COLUMN, columnKey, type ColumnDraft } from "./lib/columns";

let nextRowKey = 0;

/** The columns a custom signal declares: a label and a type per row, and
 * under each the field name a sender keys its cells on. At least one row
 * always (the last has no remove), at most the sheet's column cap. A
 * problem marks the row it is about, its cause beside it. Row keys live
 * beside the drafts, as the outputs editor keeps them, so a removal never
 * moves one row's DOM into another. */
export function ColumnsEditor({
  id,
  columns,
  onChange,
  problem,
  shown,
}: {
  id: string;
  columns: ColumnDraft[];
  onChange: (next: ColumnDraft[]) => void;
  problem: { message: string; index: number } | null;
  /** Whether the problem's marks show: only after a blocked Create. The
   * row is tagged either way, so the readiness jump finds it in the same
   * click that turns the marks on. */
  shown: boolean;
}) {
  const [rowKeys, setRowKeys] = useState<number[]>(() => columns.map(() => nextRowKey++));
  const full = columns.length >= MAX_SIGNAL_COLUMNS;
  const causeId = `${id}-gap`;

  function update(index: number, patch: Partial<ColumnDraft>) {
    onChange(columns.map((column, i) => (i === index ? { ...column, ...patch } : column)));
  }

  function add() {
    setRowKeys([...rowKeys, nextRowKey++]);
    onChange([...columns, { ...EMPTY_COLUMN }]);
  }

  function remove(index: number) {
    setRowKeys(rowKeys.filter((_, i) => i !== index));
    onChange(columns.filter((_, i) => i !== index));
  }

  return (
    <div id={id} className="space-y-3">
      {columns.map((column, index) => {
        const offending = problem !== null && problem.index === index;
        const flagged = shown && offending;
        const key = columnKey(column);
        return (
          <div key={rowKeys[index] ?? index} data-problem={offending || undefined} className="space-y-1">
            <div className="flex items-center gap-2">
              <Input
                value={column.label}
                onChange={(event) => update(index, { label: event.target.value })}
                maxLength={COLUMN_LABEL_MAX_LENGTH}
                placeholder={index === 0 ? COLUMN_PLACEHOLDER : undefined}
                aria-label={`Column ${index + 1} name`}
                aria-describedby={flagged ? causeId : undefined}
                warned={flagged}
                className="min-w-0 flex-1"
              />
              <Select
                value={column.type}
                onChange={(event) => update(index, { type: event.target.value as ColumnType })}
                className="w-32 shrink-0"
                aria-label={`Column ${index + 1} type`}
              >
                {COLUMN_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {columnTypeLabel(type)}
                  </option>
                ))}
              </Select>
              {columns.length > 1 && (
                <button
                  type="button"
                  onClick={() => remove(index)}
                  className="shrink-0 rounded-md p-1 text-faint hover:text-danger"
                  aria-label={`Remove column ${index + 1}`}
                >
                  <X aria-hidden className="h-4 w-4" />
                </button>
              )}
            </div>
            {/* The field name a sender keys cells on, as the server will
                derive it, so a clash shows here rather than at Create. */}
            {key && (
              <p className="font-mono text-xs text-faint">
                {FIELD_PREFIX} {key}
              </p>
            )}
            {flagged && (
              <FieldError id={causeId} tone="warning">
                {problem.message}
              </FieldError>
            )}
          </div>
        );
      })}
      {full ? (
        <p className="text-xs text-muted">{columnsCapNote(MAX_SIGNAL_COLUMNS)}</p>
      ) : (
        <Button type="button" variant="ghost" size="sm" onClick={add}>
          <Plus aria-hidden className="mr-1.5 h-4 w-4" />
          {ADD_COLUMN}
        </Button>
      )}
    </div>
  );
}
