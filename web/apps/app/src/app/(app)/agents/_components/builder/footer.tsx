"use client";

import { useState } from "react";
import { Eraser, FlaskConical } from "lucide-react";
import { Button, PageFooter } from "@bower/ui";

import { ConfirmDelete } from "../../../_components/confirm-delete";
import { DELETE_AGENT_CONSEQUENCE, DELETE_AGENT_QUESTION } from "../copy";
import { type ChecklistItem, ReadinessChecklist } from "../../../_components/agent-config";

/** The builder's pinned actions: the readiness checklist (its own
 * centered row on mobile), the delete confirm tier (owned here), and
 * Clear | Test | Save with the responsive split. */
export function BuilderFooter({
  checklist,
  showDelete,
  busy,
  testBusy,
  onDelete,
  onClearDraft,
  onTest,
  onSave,
}: {
  checklist: ChecklistItem[] | null;
  showDelete: boolean;
  busy: boolean;
  testBusy: boolean;
  onDelete: () => void;
  onClearDraft: () => void;
  onTest: () => void;
  onSave: () => void;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  return (
    <PageFooter>
      {checklist && <ReadinessChecklist items={checklist} />}
      {showDelete &&
        (confirmingDelete ? (
          <ConfirmDelete
            inline
            question={DELETE_AGENT_QUESTION}
            consequence={DELETE_AGENT_CONSEQUENCE}
            busy={busy}
            onCancel={() => setConfirmingDelete(false)}
            onDelete={onDelete}
          />
        ) : (
          <Button variant="ghost" onClick={() => setConfirmingDelete(true)} className="text-danger">
            Delete
          </Button>
        ))}
      {/* Desktop: Clear leads the right-aligned trio. Mobile: Clear
          holds the row's left, Test/Save push right. */}
      <Button variant="ghost" onClick={onClearDraft} className="sm:ml-auto">
        <Eraser aria-hidden className="mr-1.5 h-4 w-4" />
        Clear draft
      </Button>
      <Button variant="secondary" onClick={onTest} loading={testBusy} className="ml-auto sm:ml-0">
        <FlaskConical aria-hidden className="mr-1.5 h-4 w-4" />
        Test
      </Button>
      <Button onClick={onSave} loading={busy}>
        Save
      </Button>
    </PageFooter>
  );
}
