import type { ListColumn } from "@bower/api";

/** The key of a column an AI column create made, read from the create's
 * own reply: its LAST column. The create appends its columns at the end
 * of the sheet under the List lock, so the reply's last column is one
 * it made, and any one of them names the agent's fill (an agent's
 * columns fill as one unit). Diffing the reply against this tab's copy
 * of the columns instead would also pick up anything the tab missed
 * (another tab's column), and the drawer would fill that one. Null
 * when the reply ends in no AI column, which a create never answers. */
export function createdColumnKey(columns: readonly ListColumn[]): string | null {
  const last = columns.at(-1);
  return last?.kind === "ai" ? last.key : null;
}
