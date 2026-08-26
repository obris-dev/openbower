// The slash trigger's grammar, client-only (nothing server-side to
// mirror): "/" at the start of a word opens variable completion, the
// characters after it filter, and accepting replaces the whole
// "/partial" run with a {{key}} token.

/** The live trigger before the caret: where the "/" sits and what has
 * been typed after it. */
export type SlashContext = { start: number; query: string };

/** The "/query" run immediately before the caret, or null. A slash
 * counts only at the start of a word (start of text or after
 * whitespace), so authored text like "and/or" or a pasted URL never
 * opens the popover; the query stops at the first character that
 * could not begin or continue a variable name. */
export function slashContext(text: string, caret: number): SlashContext | null {
  const match = /(?:^|\s)(\/[A-Za-z0-9_]*)$/.exec(text.slice(0, caret));
  if (!match?.[1]) return null;
  return { start: caret - match[1].length, query: match[1].slice(1) };
}

/** The variables the popover offers for a query: prefix matches
 * first, then substring matches, source order kept within each band
 * (the caller passes keys in column order); case-insensitive, keys
 * themselves are already lowercase but queries need not be. */
export function matchVariables(keys: string[], query: string): string[] {
  const q = query.toLowerCase();
  const prefixed = keys.filter((key) => key.startsWith(q));
  return [...prefixed, ...keys.filter((key) => !key.startsWith(q) && key.includes(q))];
}

/** Accepting a completion: the "/partial" run (context.start up to
 * the caret) becomes {{key}}, and the returned caret sits just after
 * the closing braces so typing continues past the token. */
export function applyCompletion(
  text: string,
  context: SlashContext,
  caret: number,
  key: string,
): { text: string; caret: number } {
  const token = `{{${key}}}`;
  return {
    text: text.slice(0, context.start) + token + text.slice(caret),
    caret: context.start + token.length,
  };
}
