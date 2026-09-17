// The Send webhook drawer's copy: the teaching lines and the readiness
// gaps, one home so the footer's checklist and the sections say the
// same thing. "Send webhook" and "destination" are the terms this
// audience already uses for the thing, so they stay.

export const LEDE = "Pick a destination and the columns to wait for, then send one row as a test.";

export const WAIT_LEGEND = "Send once these columns complete";
export const WAIT_HINT =
  "Complete means filled, or blank with a reason. A row is sent again whenever one of these columns is filled again. Fewer columns means faster sends.";

export const PREVIEW_LEGEND = "What your receiver gets";
export const PREVIEW_HINT =
  "One row, as the test will send it. Edit a value in place; only the cells shown are sent, normalized to their column's type. Switching rows replaces your edits.";
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

export const GAP_DESTINATION = "Choose a destination.";
export const GAP_WAIT = "Choose at least one column to wait for.";

/** The stepper's line: the count is the rows this page has loaded, not
 * the sheet's, and says so once there is more than one. */
export function rowLine(position: number, count: number): string {
  return count > 1 ? `Row ${position} of ${count} loaded` : `Row ${position}`;
}
