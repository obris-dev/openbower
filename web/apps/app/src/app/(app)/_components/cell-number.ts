// The ONE home for how a number or currency cell reads, beside
// cell-moment's rule for times: display only, so editing, copy, export
// and every sender keep the stored digits.

// The stored form the contract's shape rule writes (_number and
// _currency in openbower_schema/cell_types.py): an optional leading
// symbol on a currency, an optional sign, digits with no grouping, an
// optional fraction. A value in any other form (a flagged mismatch)
// shows as it arrived.
const STORED_NUMBER = /^([$€£¥]?)([+-]?)(\d+)(\.\d+)?$/;
const NUMBER_TYPES: ReadonlySet<string> = new Set(["number", "currency"]);

/** The value with its whole part grouped in the reader's locale
 * (47000000 -> 47,000,000), or null when it renders as stored. The
 * fraction is kept exactly as written: grouping never rounds. */
export function cellNumber(type: string, value: string, locale?: string): string | null {
  if (!NUMBER_TYPES.has(type)) return null;
  const match = STORED_NUMBER.exec(value);
  if (!match) return null;
  const [, symbol = "", sign = "", whole = "", fraction = ""] = match;
  if (symbol && type !== "currency") return null;
  const format = new Intl.NumberFormat(locale, { useGrouping: true });
  // BigInt, so a whole part past 2^53 groups without losing a digit.
  const grouped = format.format(BigInt(whole));
  const point = format.formatToParts(0.5).find((part) => part.type === "decimal")?.value ?? ".";
  return `${sign}${symbol}${grouped}${fraction ? point + fraction.slice(1) : ""}`;
}
