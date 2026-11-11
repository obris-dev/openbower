/** Where a dragged column is allowed to travel.
 *
 * A drag transform is otherwise unbounded: the pointer delta is
 * applied verbatim, so a column can be carried past the last one, past
 * the "+" cell, and off the sheet entirely. */

type Span = { left: number; right: number };

/** The horizontal delta clamped so the node stays inside `bounds`.
 *
 * `x` comes back untouched when it already fits, so the common case
 * costs nothing. A node WIDER than its bounds cannot satisfy both
 * edges; it pins to the leading one, because clamping to the trailing
 * edge instead would drag the node backwards under a pointer that is
 * moving forwards. */
export function clampDragX(x: number, node: Span, bounds: Span): number {
  const min = bounds.left - node.left;
  const max = bounds.right - node.right;
  if (min > max) return min;
  return Math.min(Math.max(x, min), max);
}
