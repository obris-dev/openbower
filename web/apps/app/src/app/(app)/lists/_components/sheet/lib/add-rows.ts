/** The add-rows control's numbers. The default is the spreadsheet's
 * own (ten rows, not an invented number); the bound is the append's,
 * `MAX_ROWS_PER_ADD` on the server, restated here since the contract
 * does not carry it. */
export const ADD_ROWS_DEFAULT = 10;
export const ADD_ROWS_MAX = 1000;

/** A count pulled inside the bound: nothing, nonsense or zero asks
 * for one row rather than none, too many asks for the most. */
export function clampCount(count: number): number {
  if (!Number.isFinite(count)) return 1;
  return Math.min(Math.max(Math.round(count), 1), ADD_ROWS_MAX);
}
