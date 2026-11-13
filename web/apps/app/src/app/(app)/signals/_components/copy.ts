// The Signals tab's copy, one place.

export const TITLE = "Signals";
export const CREATE_ACTION = "Create signal";
export const EMPTY_TITLE = "No signals yet";
export const EMPTY_SUBTITLE = "A signal fills a sheet with the events you choose, like every new funding round in your market.";
export const INTRO = "Events you follow, each filling its own sheet as they arrive.";
export const NOT_YET_READ = "Not yet";
export const VIEW_ACTION = "View log";
// Edit opens a signal's settings: the bare verb on a signal's own row;
// EDIT_SIGNAL_ACTION names it where the row is its sheet.
export const EDIT_ACTION = "Edit";
export const DELETE_QUESTION = "Delete this signal?";
export const DELETED_TOAST = "Signal deleted.";

/** What a delete takes with it, said once: the signal stops, and its
 * sheet goes with every row it gathered. */
export function deleteConsequence(rows: number): string {
  if (rows === 0) return "It stops collecting events, and its sheet is deleted. This can't be undone.";
  const counted = rows === 1 ? "its 1 row" : `its ${rows.toLocaleString("en-US")} rows`;
  return `It stops collecting events, and its sheet and ${counted} are deleted. This can't be undone.`;
}

/** A signal type as a person reads it: the catalog's key, title-cased
 * (funding reads Funding). */
export function signalType(signal: string): string {
  return signal
    .split("_")
    .map((word) => (word ? word[0]!.toUpperCase() + word.slice(1) : word))
    .join(" ");
}
