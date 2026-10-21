import { Banknote, Calendar, Hash, Link2, Mail, Type, type LucideIcon } from "lucide-react";
import type { ColumnType } from "@bower/api";

/** The ONE display home for the sheet's column types: labels and
 * icons keyed by the generated schema's values (@bower/schema is the
 * value home; this file is the dress). Every surface that shows a
 * type (the Add column menu, the outputs editor's type select) reads
 * this map, so a type added server-side gets its dress in exactly one
 * place. */
export const COLUMN_TYPE_META: Record<ColumnType, { label: string; icon: LucideIcon }> = {
  text: { label: "Text", icon: Type },
  number: { label: "Number", icon: Hash },
  currency: { label: "Currency", icon: Banknote },
  date: { label: "Date", icon: Calendar },
  url: { label: "URL", icon: Link2 },
  email: { label: "Email", icon: Mail },
};

export function columnTypeLabel(type: ColumnType): string {
  return COLUMN_TYPE_META[type]?.label ?? type;
}
