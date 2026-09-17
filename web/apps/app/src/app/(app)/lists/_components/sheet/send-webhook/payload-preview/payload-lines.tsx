"use client";

import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { TouchTarget } from "@bower/ui";

import type { PreviewLine } from "../lib/preview";
import { IncludeColumn } from "./include-column";
import type { CellActions } from "./types";

const INDENT_PX = 14;
const MIN_INPUT_CH = 8;
// A long cell (an AI answer) scrolls inside its input rather than
// pushing the line's controls out of view.
const MAX_INPUT_CH = 40;

const ICON_BUTTON =
  "relative rounded p-0.5 text-faint hover:bg-surface hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-signal-600";

export function PayloadLines({ lines, cells }: { lines: PreviewLine[]; cells: CellActions }) {
  const root = useRef<HTMLDivElement>(null);
  // Excluding a cell unmounts the button that had focus, and including
  // the last missing column unmounts the menu that had it; the input at
  // that position (or the new cell's own) takes focus, so keyboard
  // users are not dropped to the body. `lines` is rebuilt every render,
  // so the effect runs each time and the null check is the real gate.
  const pendingFocus = useRef<{ index: number } | { key: string } | null>(null);
  useEffect(() => {
    const pending = pendingFocus.current;
    if (pending === null) return;
    pendingFocus.current = null;
    const inputs = Array.from(root.current?.querySelectorAll<HTMLInputElement>("input") ?? []);
    const target =
      "key" in pending
        ? inputs.find((input) => input.dataset.key === pending.key)
        : inputs[Math.min(pending.index, inputs.length - 1)];
    target?.focus();
  }, [lines]);

  function exclude(key: string, inputIndex: number) {
    pendingFocus.current = { index: inputIndex };
    cells.onRemove(key);
  }

  function include(key: string) {
    pendingFocus.current = { key };
    cells.onAdd(key);
  }

  // Each editable line's position among the inputs, for the focus
  // hand-off above.
  const inputIndexOf: number[] = [];
  let seen = 0;
  for (const line of lines) inputIndexOf.push(line.editable ? seen++ : -1);
  return (
    // Not a <pre>: every line is its own flex row, so an input and its
    // controls sit on the line their JSON key opens. One scroll region
    // (the drawer's): a cap here would nest a second one.
    <div ref={root} className="mt-1 overflow-x-auto rounded-md bg-wash p-2 font-mono text-xs leading-6 text-foreground">
      {lines.map((line, index) => {
        const thisInput = inputIndexOf[index] ?? -1;
        const label = line.editable ? (cells.labels[line.editable.key] ?? line.editable.key) : "";
        return (
          <div
            key={index}
            style={{ paddingLeft: line.depth * INDENT_PX }}
            className={line.placeholder ? "flex items-center italic text-faint" : "flex items-center gap-1"}
          >
            {line.add ? (
              <IncludeColumn cells={{ ...cells, onAdd: include }} />
            ) : (
              <>
                <span className="whitespace-pre" title={label || undefined}>
                  {line.text}
                </span>
                {line.editable && (
                  <>
                    <input
                      data-key={line.editable.key}
                      value={line.editable.value}
                      onChange={(event) => cells.onEdit(line.editable!.key, event.target.value)}
                      aria-label={`${label} (${line.editable.key})`}
                      style={{
                        width: `${Math.min(MAX_INPUT_CH, Math.max(MIN_INPUT_CH, line.editable.value.length + 2))}ch`,
                      }}
                      className="rounded bg-surface px-1 font-mono text-xs text-foreground ring-1 ring-hairline focus:outline-none focus:ring-signal-600"
                    />
                    <span className="whitespace-pre">{line.editable.suffix}</span>
                    {cells.removable && (
                      <button
                        type="button"
                        onClick={() => exclude(line.editable!.key, thisInput)}
                        aria-label={`Exclude ${label}`}
                        className={ICON_BUTTON}
                      >
                        <TouchTarget>
                          <X aria-hidden className="h-3.5 w-3.5" />
                        </TouchTarget>
                      </button>
                    )}
                  </>
                )}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
