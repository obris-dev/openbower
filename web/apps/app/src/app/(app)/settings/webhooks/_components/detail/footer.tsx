"use client";

import { useState } from "react";
import { Button, ErrorMessage, PageFooter } from "@bower/ui";

import { ConfirmDelete } from "../../../../_components/confirm-delete";
import { DELETE_DESTINATION_CONSEQUENCE, DELETE_DESTINATION_QUESTION } from "../copy";
import { FORM_ID } from "./use-destination-form";

/** The pinned actions: the delete confirm tier (owned here, it is the
 * bar that renders it), the form-level refusal banner, and Save, which
 * submits the settings form by id. `onDelete` resolves false when the
 * delete did not happen, so the tier folds back. */
export function DetailFooter({
  saving,
  serverError,
  onDelete,
}: {
  saving: boolean;
  serverError: string | null;
  onDelete: () => Promise<boolean>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function remove() {
    setDeleting(true);
    const removed = await onDelete();
    // On success the page navigates away; on failure the toast spoke,
    // and the tier folds back.
    if (!removed) {
      setDeleting(false);
      setConfirming(false);
    }
  }

  return (
    <PageFooter>
      {confirming ? (
        <ConfirmDelete
          inline
          question={DELETE_DESTINATION_QUESTION}
          consequence={DELETE_DESTINATION_CONSEQUENCE}
          busy={deleting}
          onCancel={() => setConfirming(false)}
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
