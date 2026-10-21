import { clampWidth, DEFAULT_COLUMN_WIDTH } from "./column-sizing.ts";

/** The resized widths of one sheet's columns, as they go to and come
 * back from browser storage. Keyed by column key, so a column keeps
 * its width across a rename or a reorder and loses it with its
 * delete. */
export type ColumnWidths = Record<string, number>;

/** One storage entry per sheet: a width is a fact about a column on
 * THIS sheet, and two sheets sharing a key ("name") have nothing to
 * say to each other. */
export function storageKey(listId: string): string {
  return `sheet-widths:${listId}`;
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
