import { WEBHOOK_CELL_WORD } from "./copy";

/** A webhook column's body cell: a quiet word, since nothing is in
 * flight and the column holds no value. Delivery state arrives with
 * the flush. */
export function WebhookCell() {
  return <span className="text-xs text-faint">{WEBHOOK_CELL_WORD}</span>;
}
