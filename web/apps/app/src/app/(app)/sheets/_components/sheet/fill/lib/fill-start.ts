/** Whether a fill may START at a column: its node is an entry action,
 * the action right behind its path's entry marker, which an arriving
 * row starts too (the sheet's `entry_action_ids`, derived from its
 * paths on every detail read). A column downstream of a barrier fills
 * when the workflow reaches it, so it offers no fill of its own; the
 * fill request refuses it either way. */
export function startsFill(column: { node_id: string }, entryActionIds: readonly string[]): boolean {
  return entryActionIds.includes(column.node_id);
}
