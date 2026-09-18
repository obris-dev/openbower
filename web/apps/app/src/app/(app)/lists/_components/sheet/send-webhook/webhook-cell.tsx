import { WEBHOOK_CELL_WAITING } from "@bower/api";

import { WEBHOOK_CELL_WORD } from "./copy";

/** A webhook column's body cell. The row's word comes off the row
 * itself (RenderableListRow.webhooks), like an AI cell's state: a
 * quiet Waiting once the row is complete for the columns the webhook
 * waits on and not yet sent, nothing while it is still filling, so
 * the column reads as the queue it is. A word this bundle has not
 * heard of shows nothing rather than a claim it cannot make. */
export function WebhookCell({ state }: { state: string | undefined }) {
  if (state !== WEBHOOK_CELL_WAITING) return null;
  return <span className="text-xs text-faint">{WEBHOOK_CELL_WORD}</span>;
}
