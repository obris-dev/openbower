// The builder's readiness model: which sections an attempted action
// still needs, where each lives, and how to take the user there. The
// FIELDS decide validity; this module only names and orders them.

export type SectionKey = "label" | "prompt" | "model" | "outputs";
export type Missing = Record<SectionKey, boolean>;
export type Attempt = "test" | "save" | null;

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
