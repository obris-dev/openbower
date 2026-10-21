"use client";

import { Button, ConfirmDialogTitle, ErrorMessage } from "@bower/ui";

/** The three places the ritual sits; see `layout` below. */
export type ConfirmLayout = "panel" | "inline" | "dialog";

/** THE delete confirm ritual, shared by every surface that asks one
 * (footer bars, row-menu panels, the confirm dialog): the question, an
 * optional quiet
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
  layout = "panel",
  verb = "Delete",
  refusal = null,
  onCancel,
  onDelete,
}: {
  question: string;
  consequence?: string;
  busy?: boolean;
  /** Where the ritual sits, one of three. `panel` (the default) is the
   * stacked form a roster row hangs off its verb in an anchored panel.
   * `inline` is the row layout for a bar the ritual has to itself (a
   * page footer, a toolbar's action slot): it never shares a flex line
   * with the content it confirms. `dialog` is the ConfirmDialog's
   * form: the dialog owns the width and padding, so this fills it,
   * with the question as the dialog's heading, the consequence as its
   * one line of body, and full-size buttons with a bordered Cancel,
   * the way a dialog's answers read. */
  layout?: ConfirmLayout;
  verb?: string;
  refusal?: string | null;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const buttons = (
    <>
      {/* autoFocus lands focus on the swap-in tier's Cancel;
          data-autofocus does the same under a headless panel. */}
      <Button
        autoFocus
        data-autofocus
        size={layout === "dialog" ? "md" : "sm"}
        variant={layout === "dialog" ? "outline" : "ghost"}
        onClick={onCancel}
      >
        Cancel
      </Button>
      <Button size={layout === "dialog" ? "md" : "sm"} variant="danger" onClick={onDelete} loading={busy}>
        {verb}
      </Button>
    </>
  );
  if (layout === "dialog") {
    return (
      <div>
        <ConfirmDialogTitle>{question}</ConfirmDialogTitle>
        {consequence && <p className="mt-1 text-sm leading-5 text-muted">{consequence}</p>}
        {refusal && (
          <div className="mt-3">
            <ErrorMessage message={refusal} />
          </div>
        )}
        <div className="mt-6 flex justify-end gap-3">{buttons}</div>
      </div>
    );
  }
  if (layout === "inline") {
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
