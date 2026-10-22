"use client";

import { ChevronDown, Plus } from "lucide-react";
import { Dropdown, DropdownButton, DropdownMenu } from "@bower/ui";

import { AddColumnMenuItems, type AddColumnKind } from "../add-column";
import { TRAILING_CELL_WIDTH } from "./lib/column-sizing";

/** The header's trailing cell: the spreadsheet-native place to add a
 * column, right after the last one. The same kind menu as the
 * toolbar primary, so both entry points speak one gesture. */
export function AddColumnCell({ onAddColumn }: { onAddColumn: (kind: AddColumnKind) => void }) {
  return (
    <div className="flex h-full flex-none border-b border-r border-hairline" style={{ width: TRAILING_CELL_WIDTH }}>
      <Dropdown>
        <DropdownButton className="flex h-full w-full items-center gap-1.5 whitespace-nowrap px-2.5 text-sm font-medium text-foreground hover:bg-wash">
          <Plus aria-hidden className="h-4 w-4 shrink-0" />
          Add column
          <ChevronDown aria-hidden className="h-4 w-4 shrink-0 text-faint" />
        </DropdownButton>
        <DropdownMenu anchor="bottom start">
          <AddColumnMenuItems onPick={onAddColumn} />
        </DropdownMenu>
      </Dropdown>
    </div>
  );
}
