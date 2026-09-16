"use client";

import { ArrowUpRight, Sparkles } from "lucide-react";
import { DropdownItem } from "@bower/ui";
import { COLUMN_TYPES, type ColumnType } from "@bower/api";

import { COLUMN_TYPE_META, columnTypeLabel } from "../../../../_components/column-type-meta";

/** The kind chosen at the gesture: Use AI, Send webhook, or one of the
 * sheet's OWN column types (the backend's ColumnType set, values from
 * the generated schema, dress from the one column-type-meta home). */
export type ColumnKind = "ai" | "webhook" | ColumnType;

export function kindLabel(kind: ColumnKind): string {
  if (kind === "ai") return "Use AI";
  if (kind === "webhook") return "Send webhook";
  return columnTypeLabel(kind);
}

/** The Add column menu's items, shared by the toolbar primary and the
 * grid's "+" header cell: one gesture vocabulary from every entry
 * point, and new column kinds land here once. The two outbound kinds
 * lead ("Use AI" the sheet's headline capability, "Send webhook" what
 * leaves it); the plain types follow. */
export function AddColumnMenuItems({ onPick }: { onPick: (kind: ColumnKind) => void }) {
  return (
    <>
      <DropdownItem onClick={() => onPick("ai")}>
        <span className="flex items-center gap-2">
          <Sparkles aria-hidden className="h-4 w-4 text-faint" />
          Use AI
        </span>
      </DropdownItem>
      <DropdownItem onClick={() => onPick("webhook")}>
        <span className="flex items-center gap-2">
          <ArrowUpRight aria-hidden className="h-4 w-4 text-faint" />
          Send webhook
        </span>
      </DropdownItem>
      {COLUMN_TYPES.map((type) => {
        const Icon = COLUMN_TYPE_META[type].icon;
        return (
          <DropdownItem key={type} onClick={() => onPick(type)}>
            <span className="flex items-center gap-2">
              <Icon aria-hidden className="h-4 w-4 text-faint" />
              {columnTypeLabel(type)}
            </span>
          </DropdownItem>
        );
      })}
    </>
  );
}
