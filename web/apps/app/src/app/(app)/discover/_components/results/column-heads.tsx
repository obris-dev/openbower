/** THE column definition, once: the ghost and the real table render the
 * same head, so the placeholder can never drift from the outcome it
 * promises. Sticky positioning only matters inside the results' scroll
 * region; it is inert in the ghost card. */
export function ColumnHeads() {
  return (
    <thead>
      <tr className="border-b border-hairline text-xs text-muted">
        {/* Backgrounds match the Card so scrolled rows vanish beneath,
            not through. */}
        <th className="sticky top-0 bg-surface py-2 pr-3 text-right font-medium">#</th>
        <th className="sticky top-0 bg-surface py-2 pr-4 font-medium">Name</th>
        <th className="sticky top-0 bg-surface py-2 pr-4 font-medium">Industry</th>
        <th className="sticky top-0 bg-surface py-2 pr-4 font-medium">Size</th>
        <th className="sticky top-0 bg-surface py-2 text-right font-medium">Score</th>
      </tr>
    </thead>
  );
}
