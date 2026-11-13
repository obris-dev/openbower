// The Signals tab's copy, one place.

export const TITLE = "Signals";
export const CREATE_ACTION = "Create signal";
export const EMPTY_TITLE = "No signals yet";
export const EMPTY_SUBTITLE = "A signal fills a sheet with the events you choose, like every new funding round in your market.";
export const INTRO = "Events you follow, each filling its own sheet as they arrive.";
export const NOT_YET_READ = "Not yet";
// A signal's sheet, opened from its row or its settings: the UI's word
// for what the API calls a log.
export const VIEW_SHEET_ACTION = "View sheet";
export const DELETE_ACTION = "Delete";
// Edit opens a signal's settings: the bare verb on a signal's own row;
// EDIT_SIGNAL_ACTION names it where the row is its sheet.
export const EDIT_ACTION = "Edit";
export const DELETE_QUESTION = "Delete this signal?";
export const DELETED_TOAST = "Signal deleted.";

// A signal's settings page when there is no setup to edit, said by its
// cause: the service no longer has its listener, this app version has no
// form for its type, or the service is not offering its type now.
export const GONE_NOTE = "There is no setup left to edit. Delete the signal to remove its sheet.";
export function unknownTypeNote(type: string): string {
  return `This version of the app can't edit a ${type} signal. You can still delete it.`;
}
export function notOfferedNote(type: string): string {
  return `The signals service isn't offering ${type} signals right now, so this one can't be edited. You can still delete it.`;
}

/** Why a signal's settings have no setup to edit: its listener is gone, or
 * its type is one this app version has no form for, or one the service is
 * not offering now. Never blames the app for what the service decided. */
export function noSetupNote(facts: { listenerGone: boolean; signal: string; known: boolean }): string {
  if (facts.listenerGone) return GONE_NOTE;
  const type = signalType(facts.signal);
  return facts.known ? notOfferedNote(type) : unknownTypeNote(type);
}

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
