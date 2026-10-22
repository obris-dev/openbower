import { clampWidth, DEFAULT_COLUMN_WIDTH } from "./column-sizing.ts";

/** The resized widths of one sheet's columns, as they go to and come
 * back from browser storage. Keyed by column key, so a column keeps
 * its width across a rename or a reorder and loses it with its
 * delete. */
export type ColumnWidths = Record<string, number>;

/** The three facts the pre-paint script shares with this module: it
 * finds the sheet in the URL (the first capture group is the id, a
 * ULID, on the path `webRoutes.list` builds), reads the entry under
 * `STORAGE_KEY_PREFIX + id`, and stamps `WIDTH_PROPERTY_PREFIX + id +
 * "-" + key`. The script builds the names in the browser from these
 * prefixes, and `storageKey` and `widthVariablePrefix` build the same
 * names here, so nothing is derived twice by hand. */
export const SHEET_PATH = "^/lists/([0-9A-Z]{26})$";
export const STORAGE_KEY_PREFIX = "sheet-widths:";
export const WIDTH_PROPERTY_PREFIX = "--sheet-width-";

/** One storage entry per sheet: a width is a fact about a column on
 * THIS sheet, and two sheets sharing a key ("name") have nothing to
 * say to each other. */
export function storageKey(listId: string): string {
  return `${STORAGE_KEY_PREFIX}${listId}`;
}

/** The widths worth applying from a stored string. Storage is writable
 * by anything on the origin and outlives the sheet's schema, so this
 * validates entry by entry instead of trusting a cast: a non-object,
 * a non-number, a key the sheet no longer has, and a width at the
 * default all drop out, and every survivor is clamped into bounds.
 * Anything unparseable reads as nothing stored. */
export function parseStoredWidths(raw: string | null, keys: readonly string[]): ColumnWidths {
  if (raw === null) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
  const known = new Set(keys);
  const widths: ColumnWidths = {};
  for (const [key, value] of Object.entries(parsed)) {
    if (!known.has(key) || typeof value !== "number" || !Number.isFinite(value)) continue;
    const width = clampWidth(value);
    if (width !== DEFAULT_COLUMN_WIDTH) widths[key] = width;
  }
  return widths;
}

/** The string to store, holding only the widths that differ from the
 * default: a column at the default has no fact to keep, and dropping
 * it is what lets a reset read as "never resized". */
export function serializeWidths(widths: ColumnWidths): string {
  const kept: ColumnWidths = {};
  for (const [key, value] of Object.entries(widths)) {
    const width = clampWidth(value);
    if (width !== DEFAULT_COLUMN_WIDTH) kept[key] = width;
  }
  return JSON.stringify(kept);
}

/** The custom property a stored width is stamped under before first
 * paint, one name per sheet AND column: scoped by sheet, so the
 * properties of one sheet can never be read as another's. */
export function widthVariablePrefix(listId: string): string {
  return `${WIDTH_PROPERTY_PREFIX}${listId}-`;
}

/** The one column's property: what the script stamps and a pending
 * cell reads, built here and nowhere else on this side. */
export function widthVariable(listId: string, key: string): string {
  const prefix = widthVariablePrefix(listId);
  return `${prefix}${key}`;
}

/** A cell's width until the stored widths have synced into state:
 * the stamped property if the script set one, else the model's
 * width, which is the default. Once synced the number itself is
 * rendered, so a resize is never fighting a property. The key goes
 * into the name as it is: the contract's column key grammar
 * (lowercase letters, digits and underscores) is a subset of a CSS
 * identifier, which is what keeps the declaration valid. */
export function pendingWidth(listId: string, key: string, fallback: number): string {
  const name = widthVariable(listId, key);
  return `var(${name}, ${fallback}px)`;
}
