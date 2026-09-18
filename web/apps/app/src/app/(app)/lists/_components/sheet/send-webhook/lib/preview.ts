// The payload box's lines: the server's envelope (a preview or the one
// a test sent) rendered one JSON value per line, with the cells the
// client controls marked editable so they render as inputs, and an
// add slot after them while a column is missing. Pure.

// Markers are branded with a symbol key. Column keys are user-derived,
// so a key named `editable` is legal; a plain object from a parsed
// response can never carry a symbol.
const MARK: unique symbol = Symbol("preview-marker");

/** A value the user controls (a cell of the sample): rendered as an
 * input where it sits in the payload, so editing happens on the thing
 * being sent rather than in a form elsewhere. */
export type Editable = { readonly [MARK]: "editable"; key: string; value: string };
export function editable(key: string, value: string): Editable {
  return { [MARK]: "editable", key, value };
}

/** The line after the last cell where a column can be added: present
 * only while some column is not in the payload. */
export type AddSlot = { readonly [MARK]: "add" };
export const ADD_SLOT: AddSlot = { [MARK]: "add" };

export type PreviewValue =
  | string
  | number
  | boolean
  | null
  | Editable
  | AddSlot
  | PreviewValue[]
  | { [key: string]: PreviewValue };

function markOf(value: PreviewValue): "editable" | "add" | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  if (!(MARK in value)) return null;
  return (value as { [MARK]: "editable" | "add" })[MARK];
}

function isEditable(value: PreviewValue): value is Editable {
  return markOf(value) === "editable";
}

function isAddSlot(value: PreviewValue): value is AddSlot {
  return markOf(value) === "add";
}

/** Plain JSON (a parsed response) as a preview tree. Anything JSON
 * cannot hold is shown as its string form rather than dropped. */
export function fromJson(value: unknown): PreviewValue {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value)) return value.map(fromJson);
  if (typeof value === "object") {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, fromJson(v)]));
  }
  return String(value);
}

export type EditableCells = { keys: readonly string[]; values: Record<string, string>; canAddColumns: boolean };

/** The server's envelope with the client's cells in it: `data.items[0]
 * .cells` is REPLACED by an editable entry per payload key (the
 * client's values are what `cells` will send; the server normalizes at
 * send time), plus the add slot while a column is missing. Everything
 * else (ids, timestamps, states) is the server's, untouched. An
 * envelope of another shape (no items, no cells) returns as it came,
 * with no inputs. */
export function withEditableCells(envelope: Record<string, unknown>, cells: EditableCells): PreviewValue {
  const tree = fromJson(envelope);
  const root = asObject(tree);
  if (root === null) return tree;
  const data = asObject(root.data);
  if (data === null) return tree;
  const items = data.items;
  if (!Array.isArray(items) || items.length === 0) return tree;
  const [head, ...rest] = items;
  const first = asObject(head);
  if (first === null || !("cells" in first)) return tree;
  const edited: { [key: string]: PreviewValue } = Object.fromEntries(
    cells.keys.map((key) => [key, editable(key, cells.values[key] ?? "")]),
  );
  if (cells.canAddColumns) edited[""] = ADD_SLOT;
  return { ...root, data: { ...data, items: [{ ...first, cells: edited }, ...rest] } };
}

/** A plain object node of the tree, or null for anything else (a
 * scalar, an array, a marker). fromJson never produces markers, so a
 * parsed envelope's objects are all plain. */
function asObject(value: PreviewValue | undefined): { [key: string]: PreviewValue } | null {
  if (value === undefined || typeof value !== "object" || value === null || Array.isArray(value)) return null;
  if (markOf(value) !== null) return null;
  return value as { [key: string]: PreviewValue };
}

/** One rendered line. An editable line carries its input's key and
 * value with the text split around it (`text` before, `suffix` after),
 * so the renderer places the input where the JSON value would be. */
export type PreviewLine = {
  depth: number;
  text: string;
  editable?: { key: string; value: string; suffix: string };
  /** The add-a-column line, which renders a control and no JSON. */
  add?: true;
};

/** JSON, one line per value. Indentation is the line's depth, applied
 * by the renderer, so the text stays a plain string a test can read. */
export function previewLines(value: PreviewValue): PreviewLine[] {
  const lines: PreviewLine[] = [];
  emit(lines, value, 0, "", "");
  return lines;
}

function emit(lines: PreviewLine[], value: PreviewValue, depth: number, prefix: string, suffix: string): void {
  if (isEditable(value)) {
    lines.push({ depth, text: prefix, editable: { key: value.key, value: value.value, suffix } });
    return;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      lines.push({ depth, text: `${prefix}[]${suffix}` });
      return;
    }
    lines.push({ depth, text: `${prefix}[` });
    value.forEach((item, index) => emit(lines, item, depth + 1, "", index < value.length - 1 ? "," : ""));
    lines.push({ depth, text: `]${suffix}` });
    return;
  }
  if (typeof value === "object" && value !== null) {
    // The add slot is not an entry: it renders after the last real one,
    // so that one keeps no comma and the JSON shown stays valid.
    const entries = Object.entries(value).filter(([, item]) => !isAddSlot(item));
    const addable = Object.values(value).some(isAddSlot);
    if (entries.length === 0 && !addable) {
      lines.push({ depth, text: `${prefix}{}${suffix}` });
      return;
    }
    lines.push({ depth, text: `${prefix}{` });
    entries.forEach(([key, item], index) =>
      emit(lines, item, depth + 1, `${JSON.stringify(key)}: `, index < entries.length - 1 ? "," : ""),
    );
    if (addable) lines.push({ depth: depth + 1, text: "", add: true });
    lines.push({ depth, text: `}${suffix}` });
    return;
  }
  lines.push({ depth, text: `${prefix}${JSON.stringify(value)}${suffix}` });
}
