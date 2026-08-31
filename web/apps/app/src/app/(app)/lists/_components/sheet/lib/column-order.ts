import type { ListColumn } from "@bower/api";

/** Move one column to a new index, returning the resulting KEY order.
 *
 * The key order is the whole vocabulary the reorder endpoint speaks:
 * it validates the set is unchanged and carries each column's own
 * record across, so the client never sends a column's label, type, or
 * fill member back and cannot edit one by dragging it.
 *
 * Both gestures land here. The menu moves by one and the drag moves
 * to an arbitrary index, but "the order after this move" is one
 * question, and two implementations of it would drift the moment the
 * end conditions changed. */
export function moveColumn(columns: ListColumn[], from: number, to: number): string[] {
  const keys = columns.map((column) => column.key);
  // Out of range is a NO-OP, not a clamp: the callers below refuse at
  // the ends, and a silent clamp here would turn "move left from
  // position 0" into a reorder that looks like it worked.
  if (from < 0 || from >= keys.length || to < 0 || to >= keys.length || from === to) return keys;
  const moved = [...keys];
  const [key] = moved.splice(from, 1);
  moved.splice(to, 0, key!);
  return moved;
}

/** Whether a column can move that way: false at the ends, which is
 * what the menu items read to disable themselves. */
export function canMove(columns: ListColumn[], key: string, direction: -1 | 1): boolean {
  const index = columns.findIndex((column) => column.key === key);
  if (index < 0) return false;
  const target = index + direction;
  return target >= 0 && target < columns.length;
}

/** The order after nudging one column one place, or null when it
 * cannot move. Null rather than the unchanged order, so a caller
 * cannot send a no-op request believing it did something. */
export function nudgeColumn(columns: ListColumn[], key: string, direction: -1 | 1): string[] | null {
  if (!canMove(columns, key, direction)) return null;
  const index = columns.findIndex((column) => column.key === key);
  return moveColumn(columns, index, index + direction);
}

/** The column RECORDS in the given key order, or null when the keys
 * are not exactly this sheet's set (a missing, unknown, or repeated
 * key). Null is reachable: both gestures compute their keys from a
 * render a teammate's add or delete may have outrun, and applying a
 * partial order optimistically would show a sheet the server is
 * about to refuse. */
export function columnsInKeyOrder(columns: ListColumn[], keys: string[]): ListColumn[] | null {
  if (keys.length !== columns.length || new Set(keys).size !== keys.length) return null;
  const byKey = new Map(columns.map((column) => [column.key, column]));
  const moved = keys.map((key) => byKey.get(key)).filter((column) => column !== undefined);
  return moved.length === columns.length ? moved : null;
}
