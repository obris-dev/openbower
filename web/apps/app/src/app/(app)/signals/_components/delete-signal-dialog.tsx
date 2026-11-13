"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ConfirmDialog, useToast } from "@bower/ui";
import { deleteLog, webRoutes } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { ConfirmDelete } from "../../_components/confirm-delete";
import { DELETED_TOAST, DELETE_QUESTION, deleteConsequence } from "./copy";

/** A signal deleted from its settings page: asked in the confirm dialog,
 * which holds still while the signals service answers and keeps its
 * refusal, then back to Signals. Its question renders whether open or not,
 * so the panel stays filled through the leave transition. */
export function DeleteSignalDialog({
  open,
  onClose,
  signal,
}: {
  open: boolean;
  onClose: () => void;
  signal: { id: string; label: string; rowCount: number };
}) {
  const router = useRouter();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  function close() {
    if (busy) return;
    setRefusal(null);
    onClose();
  }

  async function confirm() {
    if (busy) return;
    setBusy(true);
    setRefusal(null);
    const res = await deleteLog(signal.id);
    setBusy(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "error") {
      setRefusal(res.message);
      return;
    }
    toast.success(DELETED_TOAST, signal.label);
    router.push(webRoutes.signals);
    router.refresh();
  }

  return (
    <ConfirmDialog open={open} onClose={close}>
      <ConfirmDelete
        layout="dialog"
        question={DELETE_QUESTION}
        consequence={deleteConsequence(signal.rowCount)}
        busy={busy}
        refusal={refusal}
        onCancel={close}
        onDelete={() => void confirm()}
      />
    </ConfirmDialog>
  );
}
