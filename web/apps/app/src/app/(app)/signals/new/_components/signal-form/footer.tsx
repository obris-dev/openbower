import { Button, ErrorMessage, PageFooter } from "@bower/ui";

import { ReadinessChecklist } from "../../../../_components/readiness";
import { CREATE_ACTION } from "./copy";
import type { SignalChecklistItem } from "./setup";

/** The form's pinned actions: a create refusal leading the row, the
 * attempt's checklist once an attempt was blocked, then Create signal.
 * Test lives with the results it fills; the name is the page's title. */
export function SignalFormFooter({
  refusal,
  checklist,
  busy,
  onCreate,
}: {
  refusal: string | null;
  checklist: SignalChecklistItem[] | null;
  busy: boolean;
  onCreate: () => void;
}) {
  return (
    <PageFooter>
      {refusal && <ErrorMessage message={refusal} className="w-full" />}
      {checklist && <ReadinessChecklist items={checklist} />}
      <Button onClick={onCreate} loading={busy} className="ml-auto">
        {CREATE_ACTION}
      </Button>
    </PageFooter>
  );
}
