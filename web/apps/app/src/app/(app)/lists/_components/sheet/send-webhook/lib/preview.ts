// The live preview of what a receiver gets: the envelope as the
// drawer's current choices would send it, with the server-minted values
// marked rather than guessed, and the same line renderer for the real
// envelope once a test has been sent. Pure, so the shape and the
// rendering are tested without a renderer.

// Markers are branded with a symbol key. Column keys are user-derived,
// so a key named `placeholder` or `editable` is legal; a plain object
// from the sheet or from a parsed response can never carry a symbol.
const MARK: unique symbol = Symbol("preview-marker");

/** A value only the server sets (an id, a clock, a stored state):
 * rendered as a marker, never as a made-up literal. */
export type Placeholder = { readonly [MARK]: "placeholder" };
export const SET_WHEN_SENT: Placeholder = { [MARK]: "placeholder" };

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
  | Placeholder
  | Editable
  | AddSlot
  | PreviewValue[]
  | { [key: string]: PreviewValue };

function markOf(value: PreviewValue): "placeholder" | "editable" | "add" | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  if (!(MARK in value)) return null;
  return (value as { [MARK]: "placeholder" | "editable" | "add" })[MARK];
}

function isPlaceholder(value: PreviewValue): value is Placeholder {
  return markOf(value) === "placeholder";
}

function isEditable(value: PreviewValue): value is Editable {
  return markOf(value) === "editable";
}

function isAddSlot(value: PreviewValue): value is AddSlot {
  return markOf(value) === "add";
}

export type PreviewInput = {
  sheet: { id: string; label: string };
  waitKeys: readonly string[];
  payloadKeys: readonly string[];
  row: { id: string; position: number };
  values: Record<string, string>;
  /** Whether some column is still outside the payload. */
  canAddColumns: boolean;
};

/** The envelope a test send of these choices produces, mirroring the
 * contract's WebhookEnvelope for a digest of one item. Everything the
 * drawer knows is literal; everything the server derives is a marker. */
export function previewEnvelope(input: PreviewInput): PreviewValue {
  return {
    id: SET_WHEN_SENT,
    type: "digest",
    version: 1,
    test: true,
    timestamp: SET_WHEN_SENT,
    data: {
      type: "digest",
      sheet: { id: input.sheet.id, label: input.sheet.label },
      waited_on: [...input.waitKeys],
      items: [
        {
          event_id: SET_WHEN_SENT,
          row_id: input.row.id,
          position: input.row.position,
          completed_at: SET_WHEN_SENT,
          cells: {
            ...Object.fromEntries(input.payloadKeys.map((key) => [key, editable(key, input.values[key] ?? "")])),
            ...(input.canAddColumns ? { "": ADD_SLOT } : {}),
          },
          // The server reports only the columns that have a record, so
          // the map's keys are its to decide, not just the values.
          states: SET_WHEN_SENT,
        },
      ],
    },
  };
}

/** Plain JSON (a parsed response) as a preview tree: the same renderer
 * shows the envelope a test actually sent. Anything JSON cannot hold is
 * shown as its string form rather than dropped. */
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

/** One rendered line. A marker line is flagged; an editable line
 * carries its input's key and value with the text split around it
 * (`text` before, `suffix` after), so the renderer places the input
 * where the JSON value would be. */
export type PreviewLine = {
  depth: number;
  text: string;
  placeholder: boolean;
  editable?: { key: string; value: string; suffix: string };
  /** The add-a-column line, which renders a control and no JSON. */
  add?: true;
};

/** JSON, one line per value, with markers flagged so the renderer can
 * set them apart. Indentation is the line's depth, applied by the
 * renderer, so the text stays a plain string a test can read. */
export function previewLines(value: PreviewValue): PreviewLine[] {
  const lines: PreviewLine[] = [];
  emit(lines, value, 0, "", "");
  return lines;
}

function emit(lines: PreviewLine[], value: PreviewValue, depth: number, prefix: string, suffix: string): void {
  if (isPlaceholder(value)) {
    lines.push({ depth, text: `${prefix}set when sent${suffix}`, placeholder: true });
    return;
  }
  if (isEditable(value)) {
    lines.push({ depth, text: prefix, placeholder: false, editable: { key: value.key, value: value.value, suffix } });
    return;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      lines.push({ depth, text: `${prefix}[]${suffix}`, placeholder: false });
      return;
    }
    lines.push({ depth, text: `${prefix}[`, placeholder: false });
    value.forEach((item, index) => emit(lines, item, depth + 1, "", index < value.length - 1 ? "," : ""));
    lines.push({ depth, text: `]${suffix}`, placeholder: false });
    return;
  }
  if (typeof value === "object" && value !== null) {
    // The add slot is not an entry: it renders after the last real one,
    // so that one keeps no comma and the JSON shown stays valid.
    const entries = Object.entries(value).filter(([, item]) => !isAddSlot(item));
    const addable = Object.values(value).some(isAddSlot);
    if (entries.length === 0 && !addable) {
      lines.push({ depth, text: `${prefix}{}${suffix}`, placeholder: false });
      return;
    }
    lines.push({ depth, text: `${prefix}{`, placeholder: false });
    entries.forEach(([key, item], index) =>
      emit(lines, item, depth + 1, `${JSON.stringify(key)}: `, index < entries.length - 1 ? "," : ""),
    );
    if (addable) lines.push({ depth: depth + 1, text: "", placeholder: false, add: true });
    lines.push({ depth, text: `}${suffix}`, placeholder: false });
    return;
  }
  lines.push({ depth, text: `${prefix}${JSON.stringify(value)}${suffix}`, placeholder: false });
}
