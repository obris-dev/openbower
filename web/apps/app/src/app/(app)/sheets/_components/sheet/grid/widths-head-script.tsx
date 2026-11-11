import { StoredWidthsHeadScript } from "@bower/ui";

import { MAX_COLUMN_WIDTH, MIN_COLUMN_WIDTH } from "./lib/column-sizing";
import { SHEET_PATH, STORAGE_KEY_PREFIX, WIDTH_PROPERTY_PREFIX } from "./lib/column-widths";

/** The pre-paint script for a sheet's column widths (LAYOUT.md rule
 * 7), rendered ONCE from the root layout beside the theme and sidebar
 * scripts: on a document load of a sheet it stamps the widths this
 * browser stored for it on <html>, as the properties every cell reads
 * until the grid has synced them into state, so a reload paints the
 * resized columns at once. It finds the sheet in the URL itself, so
 * the root layout can render it knowing nothing about sheets, and a
 * page never has to (a script a page renders is inserted by React on
 * a client navigation and never runs there). The bounds are the
 * grid's own, so the script clamps exactly as the sync will. */
export function ColumnWidthsHeadScript() {
  return (
    <StoredWidthsHeadScript
      path={SHEET_PATH}
      storageKeyPrefix={STORAGE_KEY_PREFIX}
      propertyPrefix={WIDTH_PROPERTY_PREFIX}
      min={MIN_COLUMN_WIDTH}
      max={MAX_COLUMN_WIDTH}
    />
  );
}
