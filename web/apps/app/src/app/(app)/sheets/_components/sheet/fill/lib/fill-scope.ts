/** The row-scope decisions the drawer's footer and the tracker's
 * Fill next control share: how many rows a gesture covers. Every figure
 * here is the CLIENT's consent arithmetic only; the server owns the
 * true eligible count and its response is the truth. */

// The default first-N scope (binary): small enough to be a cheap
// taste of a new column, large enough to judge the answers.
export const DEFAULT_SCOPE_ROWS = 32;

export type ScopeChoice = { kind: "all" } | { kind: "first"; n: number };

/** The drawer's starting selection: First 32 when the sheet outgrows
 * the default (a taste before the whole spend), All otherwise (a
 * scope control on a sheet the default already covers is noise). */
export function defaultScopeKind(rowCount: number): "first" | "all" {
  return rowCount > DEFAULT_SCOPE_ROWS ? "first" : "all";
}

/** Authored N parsed for a metered boundary: clamped up to 1 (never
 * rejected), null only when the text holds no number at all (an empty
 * input cannot clamp). */
export function parseScopeRows(text: string): number | null {
  const n = Number.parseInt(text.trim(), 10);
  return Number.isNaN(n) ? null : Math.max(1, n);
}

/** The row count a choice actually covers on this sheet: min(N,
 * rowCount) client-side, the whole sheet for All. */
export function effectiveRows(choice: ScopeChoice, rowCount: number): number {
  return choice.kind === "all" ? rowCount : Math.min(choice.n, rowCount);
}
