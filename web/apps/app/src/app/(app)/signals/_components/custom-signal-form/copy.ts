// Creating a custom signal: rows a sender pushes, into the columns a
// person declares here.
export const CUSTOM_INTRO = "Get rows your own sender pushes, into the columns you set here.";
export const COLUMNS_TITLE = "Columns";
export const COLUMNS_HINT =
  "What each pushed row carries. A sender fills these, so nobody edits them by hand; you can still add AI columns to the sheet.";
export const COLUMN_PLACEHOLDER = "Title";
export const ADD_COLUMN = "Add column";
export function columnsCapNote(max: number): string {
  return `A signal holds at most ${max} columns.`;
}
// The checklist item labels the section it names, exactly.
export const COLUMNS_CHECK = COLUMNS_TITLE;
export const CUSTOM_NAME_HINT = "What the signal and its sheet are called.";
export const FIELD_PREFIX = "field:";
export const COLUMNS_EMPTY_GAP = "Declare at least one column.";
export const KEYLESS_GAP = "A column name needs at least one letter or number.";
export function reservedNameGap(label: string): string {
  return `"${label}" can't be used as a column name; pick another.`;
}
export function keyClashGap(key: string): string {
  return `Two columns would both be "${key}"; rename one.`;
}
