/** The grid's fixed dimensions, in CSS pixels.
 *
 * ONE place for the numbers the header, the tracker row, the body
 * rows and the virtualizer all have to agree on: a row the
 * virtualizer estimates at one height and the row component paints
 * at another scrolls the body out of step with its own positions,
 * and a header taller than the tracker row's sticky offset leaves a
 * gap the rows scroll into. */

/** Every column starts here; a resize moves it between the bounds. */
export const DEFAULT_COLUMN_WIDTH = 200;
/** Narrow enough to park a column, wide enough to keep its label
 * readable past the icon. */
export const MIN_COLUMN_WIDTH = 80;
/** Past this a cell holds a paragraph, which is the detail view's
 * job, not a grid's. */
export const MAX_COLUMN_WIDTH = 800;

/** The body row height: the virtualizer's estimate AND the row's
 * painted height, by construction. */
export const ROW_HEIGHT = 35;
/** The header band's height; the tracker row sticks right under it. */
export const HEADER_HEIGHT = 36;
/** The sticky leading gutter (the row number). */
export const GUTTER_WIDTH = 56;
/** The trailing cell: the "Add column" button in the header, an empty
 * bordered cell in every row beneath it, so the grid's right edge is
 * one straight line. */
export const TRAILING_CELL_WIDTH = 148;

/** A width pulled inside the bounds. Not-a-number maps to the
 * default rather than to a bound: a stored width that failed to parse
 * should look like no stored width at all. */
export function clampWidth(width: number): number {
  if (!Number.isFinite(width)) return DEFAULT_COLUMN_WIDTH;
  return Math.min(Math.max(Math.round(width), MIN_COLUMN_WIDTH), MAX_COLUMN_WIDTH);
}
