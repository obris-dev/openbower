// The ONE home for when a typed cell value renders as a moment, beside
// cell-link's rule for links: the sheet and the agent test preview both
// read it, so a value in the stored form shows the same way in each.

// A datetime column's stored form: UTC to the second, as the contract's
// shape rule writes it (_DATETIME_STORED in openbower_schema/cell_types.py).
// Only that form is trusted as a moment; a value in any other (a flagged
// mismatch, or a model's answer before it is stored) shows as it arrived.
const STORED_MOMENT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

/** The ISO moment a cell of the given declared type renders in the
 * reader's zone, or null when the value renders as plain text. */
export function cellMoment(type: string, value: string): string | null {
  if (type !== "datetime" || !STORED_MOMENT.test(value)) return null;
  // The shape alone admits an impossible moment: 25:00 reads as no time
  // (a blank cell), and Feb 30 rolls over to Mar 2 (a different moment).
  // Only a value that prints back as itself is one.
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return null;
  const printed = at.toISOString();
  return `${printed.slice(0, 19)}Z` === value ? value : null;
}
