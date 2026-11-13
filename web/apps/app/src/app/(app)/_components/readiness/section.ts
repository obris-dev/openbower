// A blocked action's diagnosis, shared by every form that keeps its
// action enabled and names what an attempt still needs: each item a
// section, and a way to take the person there.

/** One section an attempt needs. The label matches the section's title
 * EXACTLY: the item is the signpost to the part of the form it names. */
export type ChecklistItem<K extends string = string> = { key: K; anchor: string; label: string; missing: boolean };

/** Bring a section to the user: scroll it centered, focus its input
 * (the anchor itself, or the first one inside a card anchor, so a
 * combobox inside a card is reachable too). */
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
