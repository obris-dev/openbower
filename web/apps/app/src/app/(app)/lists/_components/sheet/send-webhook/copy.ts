// The Send webhook drawer's copy: the teaching lines and the readiness
// gaps, one home so the footer's checklist and the sections say the
// same thing. "Send webhook" and "destination" are the terms this
// audience already uses for the thing, so they stay.

export const ADD_TITLE = "Send webhook";
export const EDIT_TITLE = "Edit webhook";
export const LEDE = "Pick a destination and the columns to wait for, then add the column or send one row as a test.";
export const EDIT_LEDE = "Change where this column sends, what it waits for, and what rides along.";
export const COLUMN_NAME_LABEL = "Column name";
export const COLUMN_NAME_PLACEHOLDER = "CRM sync";
export const GAP_COLUMN_NAME = "Name this column.";
export const CADENCE_LABEL = "Send every";
export const CADENCE_HINT = "Rows that complete are batched and sent on this schedule.";
export const ENABLED_LABEL = "Sending";
export const ENABLED_HINT = "Paused keeps the column and its settings; nothing is sent until it is resumed.";
export const ADD_COLUMN_ACTION = "Add column";
export const SAVE_ACTION = "Save changes";
export const SEND_TEST_ACTION = "Send test";
export const CONFIG_FAILED_LINE = "This webhook's settings did not load.";
export const RETRY = "Retry";
export const PREVIEW_WAITING_LINE = "Choose a destination and the columns to wait for to see the payload.";
export const PREVIEW_FAILED_LINE = "The payload preview did not load.";

// The cell's words, one per wire state (the column holds no value, so
// the word is the whole cell), and the failed cell's fuller sentence.
export const CELL_WAITING = "waiting";
export const CELL_SENT = "sent";
export const CELL_FAILED = "failed";
export const CELL_FAILED_CAUSE = "The last send failed";
export const CELL_FAILED_FACT = "The destination's delivery log in Settings says why. The row is sent again when a waited-on column is filled again.";

export const WAIT_LEGEND = "Send once these columns complete";
export const WAIT_HINT =
  "Complete means filled, or blank with a reason. A row is sent again whenever one of these columns is filled again. Fewer columns means faster sends. Columns filled by one agent finish together, so they are chosen together.";

export const PREVIEW_LEGEND = "What your receiver gets";
export const PREVIEW_HINT =
  "One row, as a test will send it. Edit a value in place; only the cells shown are sent. Switching rows replaces your edits.";
export const SENT_HINT = "What your receiver got, exactly as sent.";
export const NOT_SENT_LINE = "Not yet sent";
export const EXAMPLE_PAYLOAD_TITLE = "Example payload";
export const SENT_PAYLOAD_TITLE = "Payload sent";
export const INCLUDE_COLUMN = "Include column";
export const EDIT_PAYLOAD = "Edit payload";

export const NO_AI_COLUMNS_LINE = "A webhook sends a row once its AI columns complete, and this sheet has none yet.";
export const ADD_AI_COLUMN = "Add an AI column";
export const NO_DESTINATIONS_LINE = "No destinations yet. Add one in Settings, then press Refresh.";
export const DESTINATIONS_LOADING_LINE = "Loading destinations…";
export const DESTINATIONS_FAILED_LINE = "Destinations did not load.";
export const DESTINATION_PLACEHOLDER = "Destination…";
export const NO_ROWS_LINE = "Add rows to this sheet to send a sample.";

export const EDIT_WEBHOOK_VERB = "Edit webhook";
export const DELETE_WEBHOOK_COLUMN_CONSEQUENCE = "This stops sending; nothing else changes.";

export const GAP_DESTINATION = "Choose a destination.";
export const GAP_WAIT = "Choose at least one column to wait for.";

/** The stepper's line: the count is the rows this page has loaded, not
 * the sheet's, and says so once there is more than one. */
export function rowLine(position: number, count: number): string {
  return count > 1 ? `Row ${position} of ${count} loaded` : `Row ${position}`;
}
