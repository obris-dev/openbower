// The edit drawer's words, one place (the send-webhook/copy.ts pattern).

/** The column menu's verb, and the edit drawer's title. */
export const EDIT_PROMPT_VERB = "Edit prompt";
export const EDIT_LEDE = "Changes apply to rows filled from now on. Answers already filled stay as they are.";
export const SAVED_AGENT_LEDE = "This column runs a saved agent. Changes apply to every sheet using it.";
export const SAVE_ACTION = "Save";
export const CONFIG_FAILED_LINE = "Couldn't load what fills this column.";
export const RETRY = "Retry";

/** The saved agent's link to its builder: "Edit Answerer". */
export function editAgentAction(label: string): string {
  return `Edit ${label}`;
}
