// The config editor's readiness model, shared by both routes that
// compose one: which sections an attempted action still needs, where
// each lives, and how to take the user there. The FIELDS decide
// validity; this module only names and orders them.

import type { AgentConfig, AgentOutput } from "@bower/api";

import type { Provider } from "./draft";
// `.ts` on a VALUE import, here and in every pure module under lib/:
// these run under `node --test` directly, with no bundler to resolve
// an extensionless specifier. A type-only import is erased and needs
// none.
import { isContentful, outputsProblem } from "./output-key.ts";

/** A config being composed: the provider may still be unchosen. */
export type ConfigDraft = {
  prompt: string;
  provider: Provider;
  source: string;
  model: string;
  outputs: AgentOutput[];
};

export type SectionKey = "label" | "prompt" | "model" | "outputs";
export type Missing = Record<SectionKey, boolean>;
// "fill" is the sheet drawer's attempt: the same three sections
// as a bench test (no agent name is asked for there either).
export type Attempt = "test" | "save" | "fill" | null;

export type ChecklistItem = { key: SectionKey; anchor: string; label: string; missing: boolean };

// Labels match the section titles EXACTLY (the checklist item is the
// signpost to the card it links to). Save additionally needs the name.
const SECTIONS: { key: SectionKey; anchor: string; label: string; saveOnly?: boolean }[] = [
  { key: "label", anchor: "agent-label", label: "Name", saveOnly: true },
  { key: "prompt", anchor: "agent-prompt", label: "Prompt" },
  { key: "model", anchor: "agent-model", label: "Model" },
  { key: "outputs", anchor: "agent-outputs", label: "Outputs" },
];

export function buildChecklist(attempt: Attempt, missing: Missing): ChecklistItem[] {
  return SECTIONS.filter((s) => attempt === "save" || !s.saveOnly).map((s) => ({
    key: s.key,
    anchor: s.anchor,
    label: s.label,
    missing: missing[s.key],
  }));
}

/** The first gap's anchor for the attempted action, if any. */
export function firstGap(attempt: Attempt, missing: Missing): string | undefined {
  return buildChecklist(attempt, missing).find((item) => item.missing)?.anchor;
}

/** Bring a section to the user: scroll it centered, focus its input
 * (the anchor itself, or the first one inside a card anchor, so the
 * model picker's combobox is reachable too). */
export function goToSection(anchor: string): void {
  const el = document.getElementById(anchor);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const target =
    el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
      ? el
      : // The OFFENDING row first (a duplicate-key cause lives on row
        // 3, not row 1), then any input the section holds.
        (el.querySelector<HTMLElement>("[data-problem] input, [data-problem] textarea") ??
        el.querySelector<HTMLElement>("input, textarea, select"));
  target?.focus({ preventScroll: true });
}


/** The three sections a runnable config needs, and the ONE place that
 * rule is written. Both routes that compose a config asked it
 * verbatim, and each spelled it a second time inside its own config
 * memo, so the serializer's rules moving would have left four copies
 * to find. `label` is the agents route's own requirement and stays
 * with it. */
export function configMissing(draft: ConfigDraft): Omit<Missing, "label"> {
  return {
    prompt: !draft.prompt.trim(),
    model: !draft.provider || !draft.source || !draft.model,
    outputs: !draft.outputs.some(isContentful) || outputsProblem(draft.outputs) !== null,
  };
}

/** Whether that config can be SENT: every section satisfied. The
 * memos that build one asked this by re-listing the same predicates,
 * which is the same rule twice in one file.
 *
 * A type PREDICATE, because "ready" includes having chosen a provider,
 * and saying so here is what lets a caller build the contract shape
 * without re-testing it. It narrows the draft OBJECT, so a caller
 * names one and reads its fields back. */
export function configReady(draft: ConfigDraft): draft is ConfigDraft & { provider: AgentConfig["provider"] } {
  return !Object.values(configMissing(draft)).some(Boolean);
}
