// The prompt's template grammar in ONE place, mirroring the server's
// Django engine (agents/runtime/prompts.py): a variable EXPRESSION is
// a CLOSED {{ ... }} whose root STARTS WITH A LETTER and continues
// [A-Za-z0-9_]* (case-sensitive like Django's context, so {{Company}}
// surfaces an input named Company; digit-led tokens are Django
// NUMERIC LITERALS, and underscore-led ones are refused at parse, so
// neither is ever an input), filters and
// spacing allowed; tags ({% %}) are never inputs. Every consumer
// (input extraction, chip state, insertion, removal) reads these
// operations, so the grammars cannot drift.

// ONE root pattern feeds extraction, the predicate, and removal: an
// inline copy in any of them is the drift this module exists to end.
const ROOT = "[A-Za-z][A-Za-z0-9_]*";
const EXPRESSIONS = new RegExp(`\\{\\{\\s*(${ROOT})[^}]*\\}\\}`, "g");
const ROOT_ONLY = new RegExp(`^${ROOT}$`);

/** Whether a name can BE a prompt variable root: every consumer
 * (extraction, the chip builder, insertion, removal) judges through
 * this predicate, or a sheet column keyed "2024" gets a chip that
 * inserts a silent Django numeric literal. */
export function isVariableRoot(key: string): boolean {
  return ROOT_ONLY.test(key);
}

function expressionsOf(key: string): RegExp {
  // Escaped even though roots are word-shaped: a regex built from an
  // unescaped foreign string is a trap for the next caller.
  const safe = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\s?\\{\\{\\s*${safe}(?![A-Za-z0-9_])[^}]*\\}\\}`, "g");
}

/** The prompt's root variable names, ordered, deduped. */
export function promptVariables(prompt: string): string[] {
  const keys = new Set<string>();
  for (const match of prompt.matchAll(EXPRESSIONS)) {
    if (match[1]) keys.add(match[1]);
  }
  return [...keys];
}

/** Whether any expression's root is this variable. */
export function usesVariable(prompt: string, key: string): boolean {
  return isVariableRoot(key) && expressionsOf(key).test(prompt);
}

/** Remove every expression of this variable, in any spelling
 * ({{key}}, {{ key }}, {{ key|upper }}), collapsing the space the
 * insert added. A leading-token removal drops the one space it
 * orphans; AUTHORED leading whitespace elsewhere survives. */
export function stripVariable(prompt: string, key: string): string {
  if (!isVariableRoot(key)) return prompt;
  const stripped = prompt.replace(expressionsOf(key), "");
  return /^\s/.test(prompt) ? stripped : stripped.replace(/^[ \t]/, "");
}

/** The chip's insert: a tight token, space-separated. Non-roots are
 * inert here like everywhere else. */
export function insertVariable(prompt: string, key: string): string {
  if (!isVariableRoot(key)) return prompt;
  return `${prompt}${prompt.endsWith(" ") || prompt === "" ? "" : " "}{{${key}}}`;
}
