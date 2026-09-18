"use client";

import { useState } from "react";
import { Button, ErrorMessage, PageFooter } from "@bower/ui";

import { ConfirmDelete } from "../../../../_components/confirm-delete";
import { DELETE_DESTINATION_CONSEQUENCE, DELETE_DESTINATION_QUESTION } from "../copy";
import { FORM_ID } from "./use-destination-form";

/** What a delete came to: gone (the page navigates), failed (the toast
 * spoke; the tier folds), or refused in the server's words (the tier
 * stays open and shows them). */
export type DeleteOutcome = { status: "deleted" } | { status: "failed" } | { status: "refused"; detail: string };

/** The pinned actions: the delete confirm tier (owned here, it is the
 * bar that renders it), the form-level refusal banner, and Save, which
 * submits the settings form by id. */
export function DetailFooter({
  saving,
  serverError,
  onDelete,
}: {
  saving: boolean;
  serverError: string | null;
  onDelete: () => Promise<DeleteOutcome>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  async function remove() {
    setDeleting(true);
    setRefusal(null);
    const outcome = await onDelete();
    if (outcome.status === "deleted") return;
    setDeleting(false);
    if (outcome.status === "refused") {
      setRefusal(outcome.detail);
      return;
    }
    setConfirming(false);
  }

  return (
    <PageFooter>
      {confirming ? (
        <ConfirmDelete
          inline
          question={DELETE_DESTINATION_QUESTION}
          consequence={DELETE_DESTINATION_CONSEQUENCE}
          busy={deleting}
          refusal={refusal}
          onCancel={() => {
            setConfirming(false);
            setRefusal(null);
          }}
          onDelete={() => void remove()}
        />
      ) : (
        <Button variant="ghost" onClick={() => setConfirming(true)} className="text-danger">
          Delete
        </Button>
      )}
      {serverError && <ErrorMessage message={serverError} className="sm:ml-auto" />}
      <Button type="submit" form={FORM_ID} loading={saving} className={serverError ? "" : "ml-auto"}>
        Save
      </Button>
    </PageFooter>
  );
}
