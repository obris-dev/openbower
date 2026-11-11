// The sample the Send webhook drawer pre-fills: one value per chosen
// column, from the sheet's row when it has one, blank otherwise.

export type SampleRow = { id: string; data: Record<string, string> };

/** The editable sample's starting values: the row's cell for each key,
 * "" for a key the row does not hold. Pure, so the drawer's state
 * initializer and a test read the same rule. */
export function sampleFrom(row: SampleRow | null, keys: readonly string[]): Record<string, string> {
  return Object.fromEntries(keys.map((key) => [key, row?.data[key] ?? ""]));
}

/** The request's cells: exactly the payload keys, the edited values. */
export function cellsFor(values: Record<string, string>, payloadKeys: readonly string[]): Record<string, string> {
  return Object.fromEntries(payloadKeys.map((key) => [key, values[key] ?? ""]));
}
