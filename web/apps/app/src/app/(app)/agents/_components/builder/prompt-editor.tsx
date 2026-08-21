"use client";

import { FieldError, Label, Textarea } from "@bower/ui";

import { sheetsTruncatedNote } from "../copy";
import { AGENT_PROMPT_MAX_LENGTH } from "@bower/api";
import type { ListSummary } from "@bower/api";

import { CompactSelect } from "../compact-select";
import { insertVariable, isVariableRoot, stripVariable, usesVariable } from "./template";

/** The prompt with {{token}} chips sourced from a chosen sheet's
 * columns. Chips TOGGLE: a token already in the prompt shows active
 * and clicking removes it (removal must be as discoverable as
 * insertion). */
export function PromptEditor({
  prompt,
  onChange,
  warned,
  lists,
  listsLoading = false,
  listsTruncated = false,
  sourceListId,
  onSourceList,
}: {
  prompt: string;
  onChange: (next: string) => void;
  warned: boolean;
  lists: ListSummary[];
  listsLoading?: boolean;
  listsTruncated?: boolean;
  sourceListId: string;
  onSourceList: (id: string) => void;
}) {
  const sourceList = lists.find((l) => l.id === sourceListId) ?? null;

  function toggleToken(key: string) {
    onChange(usesVariable(prompt, key) ? stripVariable(prompt, key) : insertVariable(prompt, key));
  }
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label htmlFor="agent-prompt">Prompt (runs per row; reference columns as {"{{key}}"})</Label>
        <CompactSelect
          value={sourceListId}
          onChange={(e) => onSourceList(e.target.value)}
          disabled={listsLoading}
          aria-label="Show variables from list"
        >
          <option value="">{listsLoading ? "Loading sheets…" : "Variables from…"}</option>
          {lists.map((l) => (
            <option key={l.id} value={l.id}>
              {l.label}
            </option>
          ))}
        </CompactSelect>
        {listsTruncated && <span className="text-xs text-faint">{sheetsTruncatedNote(lists.length)}</span>}
      </div>
      <Textarea
        id="agent-prompt"
        warned={warned}
        value={prompt}
        onChange={(e) => onChange(e.target.value)}
        maxLength={AGENT_PROMPT_MAX_LENGTH}
        rows={12}
        placeholder={
          "Find the most senior revenue leader at {{name}} ({{domain}}).\nPrefer VP Sales, Head of Revenue, RevOps."
        }
        className="mt-1 font-mono"
      />
      {warned && <FieldError tone="warning">Write the prompt that runs per row.</FieldError>}
      {sourceList && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-faint">Click to insert or remove:</span>
          {sourceList.columns.map((column) => {
            const usable = isVariableRoot(column.key);
            const active = usable && usesVariable(prompt, column.key);
            return (
              <button
                key={column.key}
                type="button"
                aria-pressed={active}
                disabled={!usable}
                title={usable ? undefined : "This column's key can't be a prompt variable; rename the column."}
                onClick={() => toggleToken(column.key)}
                className={
                  active
                    ? "rounded-full bg-signal-700 px-2.5 py-1 font-mono text-xs text-white hover:bg-signal-800"
                    : "rounded-full bg-signal/10 px-2.5 py-1 font-mono text-xs text-signal hover:bg-signal/20 disabled:cursor-not-allowed disabled:opacity-40"
                }
              >
                {"{{"}
                {column.key}
                {"}}"}
                {!usable && <span className="sr-only"> (this key can&rsquo;t be a prompt variable)</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
