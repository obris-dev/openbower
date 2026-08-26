"use client";

import {
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { FieldError, Label, Textarea } from "@bower/ui";

import { sheetsTruncatedNote } from "./copy";
import { AGENT_PROMPT_MAX_LENGTH } from "@bower/api";
import type { ListColumn, ListSummary } from "@bower/api";

import { CompactSelect } from "../compact-select";
import { caretPoint, placeListbox } from "./lib/caret-position";
import { applyCompletion, matchVariables, slashContext, type SlashContext } from "./lib/slash-completion";
import { insertVariable, isVariableRoot, stripVariable, usesVariable } from "./lib/template";

/** Where the editor's variable vocabulary comes from, one object so a
 * half-woven source is unrepresentable: the builder PICKS a sheet (it
 * belongs to none), while a caller already on a sheet passes that
 * sheet's columns and no selector renders. */
export type VariableSource =
  | {
      kind: "picker";
      lists: ListSummary[];
      listsLoading: boolean;
      listsTruncated: boolean;
      sourceListId: string;
      onSourceList: (id: string) => void;
    }
  | { kind: "sheet"; columns: ListColumn[] };

const LISTBOX_ID = "agent-prompt-variables";

function optionId(key: string): string {
  return `agent-prompt-variable-${key}`;
}

// Pointer coarseness picks the popover's dress (a floating mini-list
// at the caret vs a full-width dock with touch-sized rows). SSR
// snapshots fine-pointer; the client corrects at hydration, before
// any popover can open.
function subscribeCoarse(onChange: () => void): () => void {
  const query = window.matchMedia("(pointer: coarse)");
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function useCoarsePointer(): boolean {
  return useSyncExternalStore(
    subscribeCoarse,
    () => window.matchMedia("(pointer: coarse)").matches,
    () => false,
  );
}

/** The prompt with {{token}} chips sourced from the variable source's
 * columns. Chips TOGGLE: a token already in the prompt shows active
 * and clicking removes it (removal must be as discoverable as
 * insertion). Typing "/" opens the same vocabulary as an inline
 * completion popover (filter after the slash, arrows + Enter or click
 * to insert, Escape to dismiss), anchored AT THE CARET via the mirror
 * measurement (caret-position.ts), flipped above it when the visible
 * viewport below runs out, and docked full-width with touch-sized
 * rows on coarse pointers (the chips stay the touch-first insertion
 * path; the slash menu is the keyboard accelerator). */
export function PromptEditor({
  prompt,
  onChange,
  warned,
  source,
  autoFocus = false,
}: {
  prompt: string;
  onChange: (next: string) => void;
  warned: boolean;
  source: VariableSource;
  autoFocus?: boolean;
}) {
  const columns: ListColumn[] | null =
    source.kind === "sheet"
      ? source.columns
      : (source.lists.find((l) => l.id === source.sourceListId)?.columns ?? null);
  const variableKeys = useMemo(() => (columns ?? []).map((c) => c.key).filter(isVariableRoot), [columns]);

  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const listboxRef = useRef<HTMLUListElement | null>(null);
  const coarse = useCoarsePointer();
  const [slash, setSlash] = useState<SlashContext | null>(null);
  // Escape parks THIS trigger closed (keyed by where its "/" sits);
  // typing a fresh slash elsewhere opens again.
  const [dismissedStart, setDismissedStart] = useState<number | null>(null);
  // The highlight and the trigger it belongs to, ONE state object: a
  // selection made under an older trigger derives back to the first
  // option instead of needing a reset effect.
  const [selection, setSelection] = useState<{ trigger: string; index: number } | null>(null);

  const matches = useMemo(() => (slash ? matchVariables(variableKeys, slash.query) : []), [slash, variableKeys]);
  const open = slash !== null && slash.start !== dismissedStart && matches.length > 0;
  const triggerId = slash ? `${slash.start}:${slash.query}` : "";
  const activeIndex =
    selection !== null && selection.trigger === triggerId && selection.index < matches.length ? selection.index : 0;

  function syncSlash(el: HTMLTextAreaElement) {
    setSlash(slashContext(el.value, el.selectionStart ?? el.value.length));
  }

  // Anchor the listbox AT the caret, recomputed per keystroke while
  // open (`slash` changes with every one): the mirror measurement and
  // the style assignment are DOM work, done in a layout effect before
  // paint so the box never flashes at a stale spot. Space above and
  // below measure against visualViewport, the truly visible area (an
  // on-screen keyboard shrinks it; the layout viewport lies there),
  // falling back to innerHeight where it is absent.
  useLayoutEffect(() => {
    const listbox = listboxRef.current;
    const el = textareaRef.current;
    if (!open || listbox === null || el === null || slash === null) return;
    const caret = caretPoint(el, slash.start + 1 + slash.query.length);
    const rect = el.getBoundingClientRect();
    const viewport = window.visualViewport;
    const viewTop = viewport ? viewport.offsetTop : 0;
    const viewBottom = viewport ? viewport.offsetTop + viewport.height : window.innerHeight;
    const placement = placeListbox({
      caret,
      editorWidth: el.clientWidth,
      editorHeight: el.clientHeight,
      popoverWidth: listbox.offsetWidth,
      popoverHeight: listbox.offsetHeight,
      spaceBelow: viewBottom - (rect.top + caret.top + caret.lineHeight),
      spaceAbove: rect.top + caret.top - viewTop,
      coarse,
    });
    // The coarse dock keeps its class-owned inset-x width; only top
    // moves.
    listbox.style.left = coarse ? "" : `${placement.left}px`;
    listbox.style.top = `${placement.top}px`;
  }, [open, slash, matches, coarse]);

  function accept(key: string) {
    const el = textareaRef.current;
    if (!el) return;
    // Recomputed from the LIVE caret at accept time, so a caret that
    // moved since the popover opened can never splice the wrong span.
    const caret = el.selectionStart ?? el.value.length;
    const context = slashContext(el.value, caret);
    if (!context) return;
    const applied = applyCompletion(el.value, context, caret, key);
    onChange(applied.text);
    setSlash(null);
    setDismissedStart(null);
    // The caret restores after React commits the new value.
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(applied.caret, applied.caret);
    });
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (!open) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setSelection({ trigger: triggerId, index: (activeIndex + step + matches.length) % matches.length });
    } else if (event.key === "Enter") {
      const key = matches[activeIndex];
      if (key === undefined) return;
      event.preventDefault();
      accept(key);
    } else if (event.key === "Escape") {
      // The dismissal is the popover's, not the surrounding drawer's.
      event.preventDefault();
      event.stopPropagation();
      if (slash) setDismissedStart(slash.start);
    }
  }

  function toggleToken(key: string) {
    onChange(usesVariable(prompt, key) ? stripVariable(prompt, key) : insertVariable(prompt, key));
  }
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label htmlFor="agent-prompt">Prompt (runs per row; reference columns as {"{{key}}"}, type / to insert)</Label>
        {source.kind === "picker" && (
          <>
            <CompactSelect
              value={source.sourceListId}
              onChange={(e) => source.onSourceList(e.target.value)}
              disabled={source.listsLoading}
              aria-label="Show variables from list"
            >
              <option value="">{source.listsLoading ? "Loading sheets…" : "Variables from…"}</option>
              {source.lists.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.label}
                </option>
              ))}
            </CompactSelect>
            {source.listsTruncated && (
              <span className="text-xs text-faint">{sheetsTruncatedNote(source.lists.length)}</span>
            )}
          </>
        )}
      </div>
      <div className="relative mt-1">
        <Textarea
          id="agent-prompt"
          ref={textareaRef}
          warned={warned}
          value={prompt}
          autoFocus={autoFocus}
          onChange={(e) => {
            onChange(e.target.value);
            syncSlash(e.target);
          }}
          onSelect={(e) => syncSlash(e.currentTarget)}
          onBlur={() => setSlash(null)}
          onKeyDown={onKeyDown}
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={open ? LISTBOX_ID : undefined}
          aria-activedescendant={open && matches[activeIndex] !== undefined ? optionId(matches[activeIndex]) : undefined}
          maxLength={AGENT_PROMPT_MAX_LENGTH}
          rows={12}
          placeholder={
            "Find the most senior revenue leader at {{name}} ({{domain}}).\nPrefer VP Sales, Head of Revenue, RevOps."
          }
          className="font-mono"
        />
        {open && (
          <ul
            ref={listboxRef}
            id={LISTBOX_ID}
            role="listbox"
            aria-label="Insert a variable"
            className={
              coarse
                ? "absolute inset-x-0 z-20 max-h-48 overflow-y-auto rounded-md bg-surface p-1 shadow-lg ring-1 ring-hairline"
                : "absolute left-0 top-full z-20 max-h-48 w-64 overflow-y-auto rounded-md bg-surface p-1 shadow-lg ring-1 ring-hairline"
            }
          >
            {matches.map((key, index) => {
              // Coarse rows are touch targets (44px floor); fine rows
              // stay compact under the caret.
              const shape = coarse
                ? "flex min-h-11 cursor-pointer items-center rounded px-3 font-mono text-sm"
                : "cursor-pointer rounded px-2 py-1 font-mono text-xs";
              return (
                <li
                  key={key}
                  id={optionId(key)}
                  role="option"
                  aria-selected={index === activeIndex}
                  // Mousedown, not click: the textarea must keep focus
                  // through the insertion (blur would close the popover
                  // before click lands).
                  onMouseDown={(event) => {
                    event.preventDefault();
                    accept(key);
                  }}
                  onMouseEnter={() => setSelection({ trigger: triggerId, index })}
                  className={
                    index === activeIndex ? `${shape} bg-wash text-foreground` : `${shape} text-muted`
                  }
                >
                  {"{{"}
                  {key}
                  {"}}"}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {warned && <FieldError tone="warning">Write the prompt that runs per row.</FieldError>}
      {columns !== null && columns.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-faint">Click to insert or remove:</span>
          {columns.map((column) => {
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
