import { COLUMN_KEY_MAX_LENGTH, RESERVED_COLUMN_KEYS, RESERVED_OUTPUT_KEYS, RESERVED_OUTPUT_MARKER } from "@bower/api";

/** The key a column labelled `text` lands under, MIRRORING the ONE server
 * derivation (openbower_schema/sheets.py derive_column_key, which the CSV
 * importer, the blank-column add, a custom signal's declared columns and
 * the output serializer all call). Change one side and a form shows a key
 * the server does not produce: the pair is pinned by the vectors in
 * column-key.test.ts and test_column_keys.py. */
export function deriveColumnKey(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, COLUMN_KEY_MAX_LENGTH);
}

/** Whether an agent output's key collides with the answer model's own
 * attributes or wears its companions' marker (the server's
 * reserved_output_key). */
export function reservedOutputKey(key: string): boolean {
  return (RESERVED_OUTPUT_KEYS as readonly string[]).includes(key) || key.includes(RESERVED_OUTPUT_MARKER);
}

/** Whether a column key is refused at every key claim (the server's
 * new_column_key): an output's reserved keys, and the keys that shadow a
 * route. */
export function reservedColumnKey(key: string): boolean {
  return reservedOutputKey(key) || RESERVED_COLUMN_KEYS.includes(key);
}
