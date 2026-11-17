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
export function noSetupNote(facts: { listenerGone: boolean; type: string; known: boolean }): string {
  if (facts.listenerGone) return GONE_NOTE;
  const name = signalType(facts.type);
  return facts.known ? notOfferedNote(name) : unknownTypeNote(name);
}

/** What a delete takes with it, said once: the signal stops, and its
 * sheet goes with every row it gathered. */
export function deleteConsequence(rows: number): string {
  if (rows === 0) return "It stops collecting events, and its sheet is deleted. This can't be undone.";
  const counted = rows === 1 ? "its 1 row" : `its ${rows.toLocaleString("en-US")} rows`;
  return `It stops collecting events, and its sheet and ${counted} are deleted. This can't be undone.`;
}

// A custom signal follows no hosted type: its rows are pushed to it.
export const CUSTOM_TYPE_LABEL = "Custom";
// Create signal's first question, and how each answer reads.
export const CHOOSE_TYPE = "What should this signal collect?";
export const CUSTOM_CHOICE_DESCRIPTION = "Rows from your own tool, in the columns you choose.";

// A custom signal's webhook URL: where its sender pushes rows.
export const WEBHOOK_TITLE = "Webhook URL";
export const WEBHOOK_LEDE = "Send rows to this URL from any tool or script.";
export const ROTATE_URL = "Rotate URL";
export const ROTATE_URL_QUESTION = "Rotate the webhook URL?";
export const ROTATE_URL_CONSEQUENCE = "The current URL stops working at once, so update your sender with the new one.";
export const ROTATED_TOAST = "Webhook URL rotated.";
// A signal fed by nothing this bundle can name.
export const UNKNOWN_FEED_NOTE = "This version of the app can't edit this signal. You can still delete it.";

/** A signal type as a person reads it: the catalog's key, title-cased
 * (funding reads Funding). */
export function signalType(type: string): string {
  return type
    .split("_")
    .map((word) => (word ? word[0]!.toUpperCase() + word.slice(1) : word))
    .join(" ");
}
