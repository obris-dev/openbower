import { Button, ErrorMessage, PageFooter } from "@bower/ui";

import { ReadinessChecklist } from "../../../_components/readiness";
import { DELETE_ACTION } from "../copy";
import { CREATE_ACTION, SAVE_ACTION } from "./copy";
import type { SignalChecklistItem } from "./setup";

/** What the footer's actions do: Create signal for a new one; Save and
 * Delete for a saved one. */
export type FooterActions =
  | { mode: "create"; onCreate: () => void }
  | { mode: "edit"; changed: boolean; onSave: () => void; onDelete: () => void };

/** The form's pinned actions: a refusal leading the row, then Delete on a
 * saved signal, the attempt's checklist once an attempt was blocked, and
 * the primary action. Test lives with the results it fills; the name is
 * the page's title. */
export function SignalFormFooter({
  refusal,
  checklist,
  busy,
  actions,
}: {
  refusal: string | null;
  checklist: SignalChecklistItem[] | null;
  busy: boolean;
  actions: FooterActions;
}) {
  return (
    <PageFooter>
      {refusal && <ErrorMessage message={refusal} className="w-full" />}
      {actions.mode === "edit" && (
        <Button variant="ghost" onClick={actions.onDelete} disabled={busy} className="mr-auto text-danger">
          {DELETE_ACTION}
        </Button>
      )}
      {checklist && <ReadinessChecklist items={checklist} />}
      {actions.mode === "create" ? (
        <Button onClick={actions.onCreate} loading={busy} className="ml-auto">
          {CREATE_ACTION}
        </Button>
      ) : (
        <Button onClick={actions.onSave} loading={busy} disabled={!actions.changed} className="ml-auto">
          {SAVE_ACTION}
        </Button>
      )}
    </PageFooter>
  );
}
