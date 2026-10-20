"use client";

import { Button, ErrorMessage } from "@bower/ui";

/** THE delete confirm ritual, shared by every surface that swaps one
 * in (footer bars, row-menu panels): the question, an optional quiet
 * consequence line, then Cancel BEFORE the destructive verb, with
 * focus landing on Cancel. One ritual, not three hand-written tiers
 * with three button orders. A refusal the server answered (a 409, in
 * the server's words) renders IN the tier, which stays open: the user
 * reads why where they asked. `verb` names a non-delete destructive
 * act (rotating a secret) in the same ritual. */
export function ConfirmDelete({
  question,
  consequence,
  busy,
  inline = false,
  verb = "Delete",
  refusal = null,
  onCancel,
  onDelete,
}: {
  question: string;
  consequence?: string;
  busy?: boolean;
  /** Row layout instead of the stacked panel form, for a bar the
   * ritual has to itself (a page footer, a toolbar's action slot): it
   * never shares a flex line with the content it confirms. A roster
   * row hangs the stacked form off its verb in an anchored panel. */
  inline?: boolean;
  verb?: string;
  refusal?: string | null;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const buttons = (
    <>
      {/* autoFocus lands focus on the swap-in tier's Cancel;
          data-autofocus does the same under a headless panel. */}
      <Button autoFocus data-autofocus size="sm" variant="ghost" onClick={onCancel}>
        Cancel
      </Button>
      <Button size="sm" variant="danger" onClick={onDelete} loading={busy}>
        {verb}
      </Button>
    </>
  );
  if (inline) {
    return (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-sm text-muted">{question}</span>
        {consequence && <span className="text-xs text-muted">{consequence}</span>}
        {refusal && <ErrorMessage message={refusal} />}
        {buttons}
      </div>
    );
  }
  // The stacked form owns its width and padding, so every panel that
  // hosts it (a row's menu, a column header, a key's Revoke) reads the
  // same: a question card, not menu rows. The buttons sit as a footer
  // at the trailing edge, the one red verb where the eye lands last.
  return (
    <div className="w-64 px-4 py-3">
      <p className="text-sm font-medium text-foreground">{question}</p>
      {consequence && (
        // The consequence gets its own quiet line, so the question
        // never wraps mid-thought.
        <p className="mt-1 text-xs text-muted">{consequence}</p>
      )}
      {/* The answer reads before the choice, and is announced: it
          appears after a click with focus still on the verb. */}
      {refusal && (
        <div className="mt-2">
          <ErrorMessage message={refusal} />
        </div>
      )}
      <div className="mt-3 flex justify-end gap-2">{buttons}</div>
    </div>
  );
}
