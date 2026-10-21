import type { ColumnType, ListColumn } from "@bower/api";
import { ArrowUpRight, Sparkles, type LucideIcon } from "lucide-react";

import { COLUMN_TYPE_META } from "../../../../../_components/column-type-meta.ts";

/** What a header's icon names: the column's KIND when it has one
 * worth showing (what fills the column says more than what shape its
 * values take), else its value type. */
export type ColumnIconName = "ai" | "webhook" | ColumnType;

export function columnIconName(column: ListColumn): ColumnIconName {
  if (column.kind === "ai" || column.kind === "webhook") return column.kind;
  return column.type;
}

/** The icon per name: the two kinds carry the glyphs their Add column
 * entries use, and every value type reads the ONE display home for
 * types, so a header and the menu that made it can never disagree. A
 * Record over the closed union, so a value type the contract adds
 * fails the build here instead of rendering a header with no glyph. */
export const COLUMN_ICONS: Record<ColumnIconName, LucideIcon> = {
  ai: Sparkles,
  webhook: ArrowUpRight,
  text: COLUMN_TYPE_META.text.icon,
  number: COLUMN_TYPE_META.number.icon,
  currency: COLUMN_TYPE_META.currency.icon,
  date: COLUMN_TYPE_META.date.icon,
  url: COLUMN_TYPE_META.url.icon,
  email: COLUMN_TYPE_META.email.icon,
};
