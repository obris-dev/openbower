"use client";

import { Button } from "@bower/ui";

/** THE delete confirm ritual, shared by every surface that swaps one
 * in (footer bars, row-menu panels): the question, an optional quiet
 * consequence line, then Cancel BEFORE the destructive verb, with
 * focus landing on Cancel. One ritual, not three hand-written tiers
 * with three button orders. */
export function ConfirmDelete({
  question,
  consequence,
  busy,
  inline = false,
  onCancel,
  onDelete,
}: {
  question: string;
  consequence?: string;
  busy?: boolean;
  /** Row layout (a footer bar) instead of the stacked panel form. */
  inline?: boolean;
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
        Delete
      </Button>
    </>
  );
  if (inline) {
    return (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-sm text-muted">{question}</span>
        {consequence && <span className="text-xs text-muted">{consequence}</span>}
        {buttons}
      </div>
    );
  }
  return (
    <div>
      <p className="text-sm text-foreground">{question}</p>
      {consequence && (
        // The consequence gets its own quiet line, so the question
        // never wraps mid-thought.
        <p className="mt-0.5 text-xs text-muted">{consequence}</p>
      )}
      <div className="flex gap-1.5 pt-2">{buttons}</div>
    </div>
  );
}
