"use client";

import { useState, type KeyboardEvent } from "react";
import { Plus } from "lucide-react";
import { Button, Input } from "@bower/ui";

import { ADD_ROWS_DEFAULT, ADD_ROWS_MAX, clampCount } from "./lib/add-rows";

/** The line under the grid, once the end of the sheet is loaded:
 * "Add [10] more rows". Not a row of the grid and not a
 * band either: no rule above it, an outlined verb and an outlined
 * count of one height, a muted sentence, sitting a little under the
 * last row at the canvas's left edge, where it stays while the
 * columns scroll under it. The count is the person's to change and
 * keeps its value for the session; Add (or Enter in the field) posts
 * that many blank rows, to be typed into. */
export function AddRowsBar({ busy, onAdd }: { busy: boolean; onAdd: (count: number) => void }) {
  const [count, setCount] = useState(String(ADD_ROWS_DEFAULT));

  function add() {
    const n = clampCount(Number(count));
    setCount(String(n));
    onAdd(n);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    add();
  }

  return (
    <div className="sticky left-0 flex items-center gap-2 px-3 py-2 text-sm text-muted">
      <Button size="sm" variant="outline" loading={busy} onClick={add} className="gap-1.5">
        <Plus aria-hidden className="size-4" />
        Add
      </Button>
      <Input
        type="number"
        inputMode="numeric"
        min={1}
        max={ADD_ROWS_MAX}
        aria-label="Rows to add"
        value={count}
        onChange={(event) => setCount(event.target.value)}
        onKeyDown={onKeyDown}
        className="h-8 w-20 py-1 text-sm tabular-nums"
      />
      <span>more rows</span>
    </div>
  );
}
