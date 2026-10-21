import type { ListColumn } from "@bower/api";

import { COLUMN_ICONS, columnIconName } from "./lib/column-icon";

/** The glyph at the head of a column: what fills it, or what its
 * values are. Decorative beside the label, so hidden from assistive
 * tech. */
export function ColumnIcon({ column }: { column: ListColumn }) {
  const Icon = COLUMN_ICONS[columnIconName(column)];
  return <Icon aria-hidden className="h-4 w-4 shrink-0 text-muted" />;
}
