"use client";

import { Plus } from "lucide-react";
import { Dropdown, DropdownButton, DropdownItem, DropdownMenu, TouchTarget } from "@bower/ui";

import { INCLUDE_COLUMN } from "../copy";
import type { CellActions } from "./types";

/** The line after the last cell: a menu of the columns not yet in the
 * payload, label with its key beside it. */
export function IncludeColumn({ cells }: { cells: CellActions }) {
  return (
    <Dropdown>
      <DropdownButton className="relative flex items-center gap-1 rounded text-xs text-signal hover:underline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-signal-600">
        <TouchTarget>
          <Plus aria-hidden className="h-3.5 w-3.5" />
          {INCLUDE_COLUMN}
        </TouchTarget>
      </DropdownButton>
      <DropdownMenu anchor="bottom start">
        {cells.missing.map((column) => (
          <DropdownItem key={column.key} onClick={() => cells.onAdd(column.key)}>
            {column.label} <span className="ml-2 font-mono text-xs text-faint">{column.key}</span>
          </DropdownItem>
        ))}
      </DropdownMenu>
    </Dropdown>
  );
}
