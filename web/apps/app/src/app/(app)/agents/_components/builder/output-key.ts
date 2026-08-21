import { AGENT_OUTPUT_KEY_MAX_LENGTH, RESERVED_OUTPUT_KEYS, type AgentOutput } from "@bower/api";

/** The result-cell key an output will land under, MIRRORING the
 * serializer's derivation (agents/serializers.py _clean_outputs): keys
 * fall back to the slugified label, so the bench can match cells for
 * outputs whose key the user left blank (the common case before
 * save). */
export function outputKey(output: AgentOutput): string {
  return (output.key || output.label)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, AGENT_OUTPUT_KEY_MAX_LENGTH);
}

/** A row the user has started (any field non-blank). The readiness
 * model judges exactly these: an untouched trailing row is neither
 * valid nor a problem. */
export function isContentful(output: AgentOutput): boolean {
  return Boolean(output.label.trim() || output.key.trim() || output.description.trim());
}

export type OutputsProblem = { message: string; index: number };

/** The first reason the server would refuse these outputs (with the
 * OFFENDING row's index, so the readiness jump lands on it, not row
 * one), or null. Mirrors the serializer's rules (label required per
 * row, a derivable non-empty key, no reserved keys, distinct keys) so
 * readiness never approves a config the server 400s, and a
 * description-only row blocks with its cause instead of being
 * silently dropped. */
export function outputsProblem(outputs: AgentOutput[]): OutputsProblem | null {
  for (const [index, output] of outputs.entries()) {
    if (!isContentful(output)) continue;
    if (!output.label.trim()) return { message: "Every output needs a name.", index };
    if (!outputKey(output)) return { message: "An output name needs at least one letter or number.", index };
    // The server refuses keys colliding with its answer model's own
    // attributes; the message speaks the LABEL the user typed.
    if ((RESERVED_OUTPUT_KEYS as readonly string[]).includes(outputKey(output))) {
      return { message: `"${(output.label || output.key).trim()}" can't be used as an output name; pick another.`, index };
    }
  }
  const keys = outputs.map((output) => (isContentful(output) ? outputKey(output) : null));
  for (const [index, key] of keys.entries()) {
    if (key !== null && keys.indexOf(key) !== index) {
      return { message: `Two outputs would land in the same "${key}" column; rename one.`, index };
    }
  }
  return null;
}
