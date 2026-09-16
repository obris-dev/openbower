"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { FieldError, Input, PasswordInput } from "@bower/ui";
import { MAX_WEBHOOK_HEADERS, WEBHOOK_HEADER_NAME_MAX_LENGTH, WEBHOOK_HEADER_VALUE_MAX_LENGTH } from "@bower/api";

import { HEADERS_NOTE } from "./copy";
import { EMPTY_HEADER, type HeaderProblem, type HeaderRow } from "./lib/headers";

// Stable row keys OUTSIDE the rows (key={index} would bleed DOM state
// across rows on removal), the outputs-editor idiom.
let nextRowKey = 0;

/** The request headers sent with every delivery: name and value rows,
 * optional (a receiver may need none), capped by the contract. Values
 * are secrets: typed into a password field and never read back. */
export function HeadersEditor({
  id,
  rows,
  onChange,
  problem,
  refusal,
}: {
  id: string;
  rows: HeaderRow[];
  onChange: (next: HeaderRow[]) => void;
  problem: HeaderProblem | null;
  refusal?: string | null;
}) {
  const [rowKeys, setRowKeys] = useState<number[]>(() => rows.map(() => nextRowKey++));

  function update(index: number, patch: Partial<HeaderRow>) {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function add() {
    setRowKeys([...rowKeys, nextRowKey++]);
    onChange([...rows, { ...EMPTY_HEADER }]);
  }

  function remove(index: number) {
    setRowKeys(rowKeys.filter((_, i) => i !== index));
    onChange(rows.filter((_, i) => i !== index));
  }

  return (
    <div id={id} className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-foreground">Headers</p>
        {rows.length < MAX_WEBHOOK_HEADERS && (
          <button
            type="button"
            onClick={add}
            className="rounded-md p-1 text-faint hover:bg-wash hover:text-foreground"
            aria-label="Add header"
          >
            <Plus aria-hidden className="h-4 w-4" />
          </button>
        )}
      </div>
      <p className="text-xs text-muted">{HEADERS_NOTE}</p>
      {/* The add button leaves at the cap; the line says why. */}
      {rows.length >= MAX_WEBHOOK_HEADERS && (
        <p className="text-xs text-warning">A destination sends at most {MAX_WEBHOOK_HEADERS} headers.</p>
      )}
      {rows.map((row, index) => (
        <div
          key={rowKeys[index] ?? index}
          className={
            index === problem?.index
              ? "flex items-start gap-2 rounded-lg border border-warning-edge p-2"
              : "flex items-start gap-2"
          }
        >
          <div className="min-w-0 flex-1">
            <Input
              value={row.name}
              onChange={(e) => update(index, { name: e.target.value })}
              maxLength={WEBHOOK_HEADER_NAME_MAX_LENGTH}
              placeholder="Authorization"
              aria-label={`Header ${index + 1} name`}
            />
            {/* maxLength stops keystrokes SILENTLY; at the cap the
                silence gets its one-line why. */}
            {row.name.length >= WEBHOOK_HEADER_NAME_MAX_LENGTH && (
              <FieldError tone="warning">Names are capped at {WEBHOOK_HEADER_NAME_MAX_LENGTH} characters.</FieldError>
            )}
          </div>
          <div className="min-w-0 flex-1">
            <PasswordInput
              value={row.value}
              onChange={(e) => update(index, { value: e.target.value })}
              maxLength={WEBHOOK_HEADER_VALUE_MAX_LENGTH}
              placeholder="Bearer …"
              autoComplete="off"
              aria-label={`Header ${index + 1} value`}
            />
            {row.value.length >= WEBHOOK_HEADER_VALUE_MAX_LENGTH && (
              <FieldError tone="warning">Values are capped at {WEBHOOK_HEADER_VALUE_MAX_LENGTH} characters.</FieldError>
            )}
          </div>
          <button
            type="button"
            onClick={() => remove(index)}
            className="mt-2 shrink-0 rounded-md p-1 text-faint hover:text-danger"
            aria-label={`Remove header ${index + 1}`}
          >
            <X aria-hidden className="h-4 w-4" />
          </button>
        </div>
      ))}
      {problem && <FieldError tone="warning">{problem.message}</FieldError>}
      {refusal && <FieldError>{refusal}</FieldError>}
    </div>
  );
}
