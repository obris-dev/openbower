"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { Card, FieldError, Input, Select } from "@bower/ui";
import { columnTypeLabel } from "../column-type-meta";
import { outputKey } from "./lib/output-key";
import {
  AGENT_OUTPUT_DESCRIPTION_MAX_LENGTH,
  AGENT_OUTPUT_LABEL_MAX_LENGTH,
  AGENT_OUTPUT_TYPES,
  MAX_AGENT_OUTPUTS,
  type AgentOutput,
} from "@bower/api";
export const EMPTY_OUTPUT: AgentOutput = { key: "", label: "", type: "text", description: "" };

/** The declared output schema: named, DESCRIBED fields the model must
 * fill (strict JSON), one column each. At least one always: the last
 * row can be replaced, never removed, and a new agent starts with one
 * EMPTY row whose placeholders do the teaching. */
let nextRowKey = 0;

export function OutputsEditor({
  outputs,
  onChange,
  warned,
  cause,
  problemIndex = null,
}: {
  outputs: AgentOutput[];
  onChange: (next: AgentOutput[]) => void;
  warned: boolean;
  cause: string | null;
  problemIndex?: number | null;
}) {
  // Stable row keys OUTSIDE the outputs (key={index} would bleed DOM
  // state across rows on removal): add/remove flow through these
  // handlers so the parallel list stays aligned, and a WHOLESALE
  // replacement (Clear draft) remounts the editor via the builder's
  // epoch key, re-running the initializer.
  const [rowKeys, setRowKeys] = useState<number[]>(() => outputs.map(() => nextRowKey++));

  function update(index: number, patch: Partial<AgentOutput>) {
    onChange(outputs.map((o, i) => (i === index ? { ...o, ...patch } : o)));
  }

  function add() {
    setRowKeys([...rowKeys, nextRowKey++]);
    onChange([...outputs, { ...EMPTY_OUTPUT }]);
  }

  function remove(index: number) {
    setRowKeys(rowKeys.filter((_, i) => i !== index));
    onChange(outputs.filter((_, i) => i !== index));
  }

  return (
    <Card id="agent-outputs" className={warned ? "space-y-3 p-4 ring-1 ring-inset ring-warning-edge" : "space-y-3 p-4"}>
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-faint">Outputs</p>
        {outputs.length < MAX_AGENT_OUTPUTS && (
          <button
            type="button"
            onClick={add}
            className="rounded-md p-1 text-faint hover:bg-wash hover:text-foreground"
            aria-label="Add output"
          >
            <Plus aria-hidden className="h-4 w-4" />
          </button>
        )}
      </div>
      <p className="text-xs text-muted">Each output is a named field the model fills, landing as its own column.</p>
      {warned && (
        <p className="text-xs text-warning">
          {cause ??
            "Name at least one output: it becomes the column a run fills, and its description tells the model what to extract."}
        </p>
      )}
      {outputs.map((output, index) => (
        <div
          key={rowKeys[index] ?? index}
          // The readiness jump lands on THIS row when it is the cause.
          data-problem={index === problemIndex || undefined}
          className={
            index === problemIndex
              ? "space-y-1.5 rounded-lg border border-warning-edge p-2.5"
              : "space-y-1.5 rounded-lg border border-hairline p-2.5"
          }
        >
          <div className="flex items-center gap-2">
            <Input
              value={output.label}
              onChange={(e) => update(index, { label: e.target.value })}
              maxLength={AGENT_OUTPUT_LABEL_MAX_LENGTH}
              placeholder="Answer"
              aria-label={`Output ${index + 1} name`}
              className="min-w-0 flex-1"
            />
            <Select
              value={output.type}
              onChange={(e) => update(index, { type: e.target.value as AgentOutput["type"] })}
              className="w-28 shrink-0"
              aria-label={`Output ${index + 1} type`}
            >
              {AGENT_OUTPUT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {columnTypeLabel(t)}
                </option>
              ))}
            </Select>
            {outputs.length > 1 && (
              <button
                type="button"
                onClick={() => remove(index)}
                className="shrink-0 rounded-md p-1 text-faint hover:text-danger"
                aria-label={`Remove output ${index + 1}`}
              >
                <X aria-hidden className="h-4 w-4" />
              </button>
            )}
          </div>
          <Input
            value={output.description}
            onChange={(e) => update(index, { description: e.target.value })}
            maxLength={AGENT_OUTPUT_DESCRIPTION_MAX_LENGTH}
            placeholder="Describe what belongs here; the model reads this to extract the right value"
            aria-label={`Output ${index + 1} definition`}
          />
          {/* maxLength stops keystrokes SILENTLY; at the cap the
              silence gets its one-line why. */}
          {output.label.length >= AGENT_OUTPUT_LABEL_MAX_LENGTH && (
            <FieldError tone="warning">Names are capped at {AGENT_OUTPUT_LABEL_MAX_LENGTH} characters.</FieldError>
          )}
          {output.description.length >= AGENT_OUTPUT_DESCRIPTION_MAX_LENGTH && (
            <FieldError tone="warning">
              Descriptions are capped at {AGENT_OUTPUT_DESCRIPTION_MAX_LENGTH} characters.
            </FieldError>
          )}
          {/* The DERIVED column key, always visible: it is what cells
              land under, and its own (shorter) cap would otherwise
              truncate invisibly until two long names collided. */}
          {outputKey(output) && (
            <p className="font-mono text-xs text-faint">column: {outputKey(output)}</p>
          )}
        </div>
      ))}
    </Card>
  );
}
