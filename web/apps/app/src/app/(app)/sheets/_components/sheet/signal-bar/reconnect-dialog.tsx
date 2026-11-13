"use client";

import { useState } from "react";
import { ConfirmDialog, useToast } from "@bower/ui";
import { reconnectLogs } from "@bower/api";
import { useUser } from "@bower/auth";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { ConfirmDelete } from "../../../../_components/confirm-delete";
import { leftLiveNote, RECONNECTED_TOAST, RECONNECT_ACTION, RECONNECT_CONSEQUENCE, RECONNECT_QUESTION } from "./copy";

/** Reconnect, asked in the confirm dialog: it waits on the identity
 * service (a fresh sync key is minted) and can be refused there (the
 * account holds as many keys as it may), so the question holds still and
 * a refusal reads in it. On success the signal is read again. */
export function ReconnectDialog({
  open,
  onClose,
  refresh,
  onReconnected,
}: {
  open: boolean;
  onClose: () => void;
  refresh: () => Promise<boolean>;
  /** After a reconnect has been read back: the pill or banner that opened
   * the dialog is gone with the status, so focus lands somewhere that
   * stays. */
  onReconnected: () => void;
}) {
  const toast = useToast();
  const { user } = useUser();
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  function close() {
    if (busy) return;
    setRefusal(null);
    onClose();
  }

  async function reconnect() {
    setBusy(true);
    setRefusal(null);
    const res = await reconnectLogs();
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "error") {
      setBusy(false);
      setRefusal(res.message);
      return;
    }
    const note = leftLiveNote(res.data.left_live_user_id, user?.id ?? null);
    toast.success(note ? `${RECONNECTED_TOAST} ${note}` : RECONNECTED_TOAST);
    setBusy(false);
    onClose();
    await refresh();
    onReconnected();
  }

  return (
    <ConfirmDialog open={open} onClose={close}>
      <ConfirmDelete
        layout="dialog"
        question={RECONNECT_QUESTION}
        consequence={RECONNECT_CONSEQUENCE}
        verb={RECONNECT_ACTION}
        variant="primary"
        busy={busy}
        refusal={refusal}
        onCancel={close}
        onDelete={() => void reconnect()}
      />
    </ConfirmDialog>
  );
}
