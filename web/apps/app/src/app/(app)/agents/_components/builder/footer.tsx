"use client";

import { useState } from "react";
import { Check, Eraser, FlaskConical, X } from "lucide-react";
import { Button, PageFooter } from "@bower/ui";

import { ConfirmDelete } from "../../../_components/confirm-delete";
import { DELETE_AGENT_CONSEQUENCE, DELETE_AGENT_QUESTION } from "../copy";
import { goToSection, type ChecklistItem } from "./readiness";

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
      {checklist && (
        // Full-width and CENTERED on mobile (its own composed row
        // above the actions); inline left on desktop.
        <div
          role="group"
          className="flex w-full flex-wrap items-center justify-center gap-3 text-xs sm:w-auto sm:justify-start"
          aria-label="What this action still needs"
        >
          {checklist.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => goToSection(item.anchor)}
              className={
                item.missing
                  ? "inline-flex items-center gap-1 text-warning hover:underline"
                  : "inline-flex items-center gap-1 text-muted"
              }
            >
              {item.missing ? <X aria-hidden className="h-3 w-3" /> : <Check aria-hidden className="h-3 w-3" />}
              {item.label}
              {item.missing && <span className="sr-only"> (missing)</span>}
            </button>
          ))}
        </div>
      )}
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
