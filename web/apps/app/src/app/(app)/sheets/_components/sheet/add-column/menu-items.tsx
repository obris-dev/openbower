"use client";

import { ArrowUpRight, Sparkles, type LucideIcon } from "lucide-react";
import { ActionMenuItem, DropdownItem } from "@bower/ui";
import { COLUMN_TYPES, type ColumnType } from "@bower/api";

import { COLUMN_TYPE_META, columnTypeLabel } from "../../../../_components/column-type-meta";

/** The kind chosen at the gesture: Use AI, Send webhook, or one of the
 * sheet's OWN column types (the backend's ColumnType set, values from
 * the generated schema, dress from the one column-type-meta home). */
export type AddColumnKind = "ai" | "webhook" | ColumnType;

/** The gesture's one name, wherever it opens the choices. */
export const ADD_COLUMN_LABEL = "Add column";

type AddColumnChoice = { kind: AddColumnKind; label: string; Icon: LucideIcon };

/** The Add column choices, in their one order for every entry point: the
 * two outbound kinds lead ("Use AI" the sheet's headline capability,
 * "Send webhook" what leaves it); the plain types follow. New column
 * kinds land here once. */
const CHOICES: AddColumnChoice[] = [
  { kind: "ai", label: "Use AI", Icon: Sparkles },
  { kind: "webhook", label: "Send webhook", Icon: ArrowUpRight },
  ...COLUMN_TYPES.map((type) => ({ kind: type, label: columnTypeLabel(type), Icon: COLUMN_TYPE_META[type].icon })),
];

function ChoiceLabel({ choice }: { choice: AddColumnChoice }) {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <choice.Icon aria-hidden className="h-4 w-4 shrink-0 text-faint" />
      <span className="truncate">{choice.label}</span>
    </span>
  );
}

/** The choices in the grid's "+" header cell's menu. */
export function AddColumnMenuItems({ onPick }: { onPick: (kind: AddColumnKind) => void }) {
  return (
    <>
      {CHOICES.map((choice) => (
        <DropdownItem key={choice.kind} onClick={() => onPick(choice.kind)}>
          <ChoiceLabel choice={choice} />
        </DropdownItem>
      ))}
    </>
  );
}

/** The same choices in the sheet menu's Add column submenu. A pick opens
 * something taking focus (a drawer, the new column's name field), so it
 * runs once the menu has closed and focus is back on its button, which
 * the drawer then returns to. */
export function AddColumnActionItems({ onPick }: { onPick: (kind: AddColumnKind) => void }) {
  return (
    <>
      {CHOICES.map((choice) => (
        <ActionMenuItem key={choice.kind} onSelectAfterClose={() => onPick(choice.kind)}>
          <ChoiceLabel choice={choice} />
        </ActionMenuItem>
      ))}
    </>
  );
}
